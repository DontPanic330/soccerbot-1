import asyncio
import json

from discord import Channel, Embed
from typing import Dict, Union, Tuple, List
from collections import OrderedDict
from django.core.exceptions import ObjectDoesNotExist
import logging
from json.decoder import JSONDecodeError
from datetime import datetime, timedelta
from pytz import UTC
import os
import re

from database.models import Match, MatchEvents, MatchEventIcon
from api.calls import makeMiddlewareCall, DataCalls
from discord_handler.client import client, toDiscordChannelName
from support.helper import task

logger = logging.getLogger(__name__)
path = os.path.dirname(os.path.realpath(__file__))


class MatchEventData:
    def __init__(self, event: MatchEvents, minute: str, team: str, player: str, playerTo: str):
        self.event = event
        self.minute = minute
        self.team = team
        self.player = player
        self.playerTo = playerTo

    def __str__(self):
        return f"Event: {self.event}, minute {self.minute}, team {self.team}, player {self.player}" \
               f", playerTo {self.playerTo}"


class LiveMatch:
    eventStyleSheet = {}
    lineupStyleSheet = {}
    emojiSet = dict([(i.name,str(i)) for i in client.get_all_emojis()])

    def __init__(self, match: Match):
        self.match = match
        self.passed = False
        self.running = False
        self.started = False
        try:
            homeTeam = match.home_team.clear_name
            awayTeam = match.away_team.clear_name
        except AttributeError:
            homeTeam = ""
            awayTeam = ""
        self.title = f"**{homeTeam}** - : - **{awayTeam}**"
        self.goalList = []
        self.runningStarted = False
        self.lock = asyncio.Event(loop=client.loop)
        self.lock.set()

    @staticmethod
    def styleSheetEvents(key: str = None) -> Union[Dict, str]:
        if LiveMatch.eventStyleSheet == {}:
            with open(path + "/../stylesheets/game_events.json") as f:
                LiveMatch.eventStyleSheet = json.loads(f.read())

        if key == None:
            return LiveMatch.eventStyleSheet
        else:
            try:
                return LiveMatch.eventStyleSheet[key]
            except KeyError:
                logger.error(f"Key {key} not available in stylesheet")
                return ""

    @staticmethod
    def styleSheetLineups(key: str = None) -> Union[Dict, str]:
        if LiveMatch.eventStyleSheet == {}:
            with open(path + "/../stylesheets/lineups.json") as f:
                LiveMatch.lineupStyleSheet = json.loads(f.read())

        if key == None:
            return LiveMatch.lineupStyleSheet
        else:
            try:
                return LiveMatch.lineupStyleSheet[key]
            except KeyError:
                logger.error(f"Key {key} not available in stylesheet")
                return ""

    @task
    async def runMatchThread(self):
        """
        Start a match threader for a given match. Will read the live data from the middleWare API (data.fifa.com) every
        20 seconds and post the events to the channel that corresponds to the match. This channel has to be created
        previously.
        :param match: Match object.  Will  post to discord channels if the object is a database.models.Match object
        """
        if self.runningStarted:
            logger.warning(f"Match {self.title} already started!")
            return
        else:
            logger.info(f"Starting match {self.title}")
        self.runningStarted = True
        pastEvents = []
        eventList = []
        sleepTime = 600
        endCycles = 10

        matchid = self.match.id
        channelName = toDiscordChannelName(f"{self.match.competition.clear_name} Matchday {self.match.matchday}")

        lineupsPosted = False
        while True:
            try:
                data = makeMiddlewareCall(DataCalls.liveData + f"/{matchid}")
            except JSONDecodeError:
                break

            if data["match"]["isFinished"] and not self.running:
                logger.info(f"Match {self.match} allready passed")
                break

            self.running = True
            if data["match"]["isLive"]:
                self.started = True
            else:
                self.started = False

            if not self.lock.is_set():
                self.lock.set()

            if not lineupsPosted and data["match"]["hasLineup"]:
                logger.info(f"Posting lineups for {self.title}")
                await asyncio.sleep(5)
                try:
                    for channel in client.get_all_channels():
                        if channel.name == channelName:
                            await LiveMatch.postLineups(channel, self.match, data["match"])
                            lineupsPosted = True
                            sleepTime = 20
                except RuntimeError:
                    lineupsPosted = False
                    logger.warning("Size of channels has changed")
            else:
                if not lineupsPosted:
                    logger.info(f"Lineups not yet available for {self.title}")

            newEvents, pastEvents = LiveMatch.parseEvents(data["match"]["events"], pastEvents)
            eventList += newEvents


            for i in eventList:
                try:
                    for channel in client.get_all_channels():
                        if channel.name == channelName:
                            self.started = True
                            self.title, goalString = await LiveMatch.sendMatchEvent(channel, self.match, i)
                            self.goalList.append(goalString)
                            try:
                                eventList.remove(i)
                            except ValueError:
                                pass
                            logger.info(f"Posting event: {i}")
                except RuntimeError:
                    logger.warning("Size of channels has changed!")
                    break

            if self.lock.is_set():
                self.lock.clear()

            if data["match"]["isFinished"]:
                if endCycles <= 0:
                    logger.info(f"Match {match} finished!")
                    break
                endCycles -= 1

            await asyncio.sleep(sleepTime)

        now = datetime.utcnow().replace(tzinfo=UTC)
        if now < (self.match.date + timedelta(hours=3)).replace(tzinfo=UTC):
            self.passed = True
        self.running = False
        self.started = False
        self.runningStarted = False
        self.lock.set()
        logger.info(f"Ending match {self.title}")

    @staticmethod
    async def postLineups(channel: Channel, match: Match, data: Dict):
        lineup = OrderedDict()
        for i in ['home', 'away']:
            lineup[i] = OrderedDict()
            lineup[i]['starting'] = []
            lineup[i]['bench'] = []
            lineup[i]['coach'] = []
            for player in data['lineups']['teams'][i]:
                playerInfo = OrderedDict()
                playerInfo['name'] = player['personName']
                playerInfo['number'] = player['shirtNumber']
                playerInfo['captain'] = player['isCaptain']
                playerInfo['gk'] = player['isGoalKeeper']

                if player['isCoach']:
                    lineup[i]['coach'].append(playerInfo)
                elif player['startingLineUp']:
                    lineup[i]['starting'].append(playerInfo)
                else:
                    lineup[i]['bench'].append(playerInfo)

        def getLineupPlayerString(teamString):
            def listPlayers(position):
                fullLineupString = ""
                for startingPlayer in lineup[teamString][position]:
                    lineupString = LiveMatch.styleSheetLineups("PlayerTemplate")
                    lineupString = lineupString.replace("$number$",str(startingPlayer['number']))
                    lineupString = lineupString.replace("$player$",startingPlayer['name'])
                    if startingPlayer['gk']:
                        lineupString = lineupString.replace("$gkTemplate$",LiveMatch.styleSheetLineups("GKTemplate"))
                    else:
                        lineupString = lineupString.replace("$gkTemplate$","")
                    if startingPlayer['captain']:
                        lineupString = lineupString.replace("$captainTemplate$", LiveMatch.styleSheetLineups("CaptainTemplate"))
                    else:
                        lineupString =  lineupString.replace("$captainTemplate$", "")
                    fullLineupString +=lineupString
                return fullLineupString

            lineupString = LiveMatch.styleSheetLineups("Layout")
            lineupString = lineupString.replace("$playerTemplate$",listPlayers('starting'))
            coachString = LiveMatch.styleSheetLineups("CoachTemplate")
            coachString = coachString.replace("$coach$",lineup[teamString]['coach'][0]['name'])
            lineupString = lineupString.replace("$coachTemplate$",coachString)
            return lineupString

        homeString = getLineupPlayerString('home')
        awayString = getLineupPlayerString('away')

        title = LiveMatch.styleSheetLineups("cardTitle")
        description = LiveMatch.styleSheetLineups("cardDescription")
        description = description.replace("$home_team$",match.home_team.clear_name)
        description = description.replace("$away_team$", match.away_team.clear_name)

        embObj = Embed(title=title,
                       description=description)

        teamTitle = LiveMatch.styleSheetLineups("TeamTitle")
        homeTeamTitle = teamTitle.replace("$team$",match.home_team.clear_name)
        awayTeamTitle = teamTitle.replace("$team$",match.away_team.clear_name)

        embObj.add_field(name=homeTeamTitle, value=homeString)
        embObj.add_field(name=awayTeamTitle, value=awayString)

        try:
            await client.send_message(channel, embed=embObj)
        except:
            await asyncio.sleep(10)
            for i in client.get_all_channels():
                if channel.name == i.name:
                    await client.send_message(channel, embed=embObj)

    #todo should this really be async?
    @staticmethod
    async def beautifyEvent(event, match):
        data = makeMiddlewareCall(DataCalls.liveData + f"/{match.id}")['match']
        homeTeam = data['teamHomeName']
        awayTeam = data['teamAwayName']

        if event.event == MatchEvents.goal:
            if event.team == homeTeam:
                goalString = LiveMatch.styleSheetEvents(MatchEvents.goalTallyHomeScore.value)
            else:
                goalString = LiveMatch.styleSheetEvents(MatchEvents.goalTallyAwayScore.value)
        else:
            goalString = LiveMatch.styleSheetEvents(MatchEvents.goalTally.value)

        title = LiveMatch.styleSheetEvents(MatchEvents.title.value)

        replaceDict = OrderedDict()
        replaceDict["$tally$"] = goalString
        replaceDict["$homeScore$"]=data['scoreHome']
        replaceDict["$awayScore$"]=data['scoreAway']
        replaceDict["$homeTeam$"]=homeTeam
        replaceDict["$awayTeam$"]=awayTeam
        for key,val in replaceDict.items():
            title = title.replace(str(key),str(val))

        replaceDict = {
            "$minute$":event.minute,
            "$player$":event.player,
            "$playerTo$": event.playerTo,
            "$team$":event.team,
        }

        content = LiveMatch.styleSheetEvents(event.event.value)

        foundEmojis = re.findall(r':[\w\d_-]+:',content)

        logger.debug(f"found emojis : {foundEmojis}")

        for i in foundEmojis:
            if i.replace(":","") in LiveMatch.emojiSet.keys():
                logger.debug(f"Replacing {i} for {LiveMatch.emojiSet[i.replace('':'','')]}")
                content.replace(i,LiveMatch.emojiSet[i.replace(":","")])
            else:
                logger.debug(f"{i} not in emojilist, replacing it with nothing")
                content.replace(i, "")

        for key,val in replaceDict.items():
            content = content.replace(key,str(val))

        goalListing = ""
        if event.event == MatchEvents.goal:
            goalListing = content + f" {event.player}"

        return title, content, goalListing

    @staticmethod
    async def sendMatchEvent(channel: Channel, match: Match, event: MatchEventData):
        """
        This function encapsulates the look and feel of the message that is sent when a matchEvent happens.
        It will build the matchString, the embed object, etc. and than send it to the appropiate channel.
        :param channel: The channel where we want to send things to
        :param match: The match that this message applies to (Metadata!)
        :param event: The actual event that happened. It consists of a MatchEvents enum and a DataDict, which in
        itself contains the minute, team and player(s) the event applies to.
        """

        title, content, goalString = await LiveMatch.beautifyEvent(event, match)
        embObj = Embed(title=title, description=content)
        embObj.set_author(name=match.competition.clear_name)

        try:
            await client.send_message(channel, embed=embObj)
        except:
            await asyncio.sleep(10)
            for i in client.get_all_channels():
                if i.name == channel.name:
                    logger.debug(f"Sending {embObj} to {i.name}")
                    await client.send_message(i, embed=embObj)

        return title, goalString

    @staticmethod
    def parseEvents(data: list, pastEvents=list) -> Tuple[List[MatchEventData], List]:
        """
        Parses the event list from the middleware api. The code below should be self explanatory, every eventCode
        represents a certain event.
        :param data: data that is to be parsed
        :param pastEvents: all events that already happened
        :return: Returns two lists: the events that are new, as well as a full list of all events that already happened
        including the new ones.
        """
        retEvents = []
        if data != pastEvents:
            diff = [i for i in data if i not in pastEvents]

            diffStr =[]

            for i in diff:
                diffStr.append(json.dumps(i))

            diffStr = list(set(diffStr)) #uniqueElements

            diff = []

            for i in diffStr:
                diff.append(json.loads(i))

            for event in reversed(diff):
                eventData = MatchEventData(event=MatchEvents.none,
                                           minute=event['minute'],
                                           team=event['teamName'],
                                           player=event['playerName'],
                                           playerTo=event['playerToName'],
                                           )
                if event['eventCode'] == 3:  # Goal!
                    eventData.event = MatchEvents.goal
                elif event['eventCode'] == 4:  # Substitution!
                    eventData.event = MatchEvents.substitution
                elif event['eventCode'] == 1:
                    ev = MatchEvents.yellowCard if event['eventDescriptionShort'] == "Y" else MatchEvents.redCard
                    eventData.event = ev
                elif event['eventCode'] == 2:
                    eventData.event = MatchEvents.yellowRedCard
                elif event['eventCode'] == 5:
                    eventData.event = MatchEvents.missedPenalty
                elif event['eventCode'] == 14:
                    ev = MatchEvents.firstHalfEnd if event[
                                                 'phaseDescriptionShort'] == "1H" else MatchEvents.secondHalfEnd
                    eventData.event = ev
                elif event['eventCode'] == 13:
                    ev = MatchEvents.kickoffFirstHalf if event[
                                                     'phaseDescriptionShort'] == "1H" else MatchEvents.kickoffSecondHalf
                    eventData.event = ev
                else:
                    logger.error(f"EventId {event['eventCode']} with descr {event['eventDescription']} not handled!")
                    logger.error(f"TeamName: {event['teamName']}")
                    continue
                retEvents.append(eventData)
            pastEvents = data
        return retEvents, pastEvents

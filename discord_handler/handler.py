import logging
from datetime import timedelta, timezone, datetime
import asyncio
from discord import Server, Client, Channel, Embed
from typing import Union, Tuple, List
from enum import Enum

from database.models import *
from database.handler import updateOverlayData, updateMatches, getNextMatchDayObjects, getCurrentMatches
from api.calls import makeMiddlewareCall, DataCalls
from database.handler import updateMatchesSingleCompetition, getAllSeasons, getAndSaveData

client = Client()

logger = logging.getLogger(__name__)


class MatchEvents(Enum):
    none = 0
    kickoffFirstHalf = 1,
    kickoffSecondHalf = 2,
    firstHalfEnd = 3,
    secondHalfEnd = 4,
    matchOver = 5,
    goal = 6,
    yellowCard = 7,
    redCard = 8,
    substitution = 9,
    missedPenalty = 10
    ownGoal = 11,
    scoredPenalty = 12


class MatchEventData:
    def __init__(self, event: MatchEvents , minute: str, team: str, player: str, playerTo : str):
        self.event = event
        self.minute = minute
        self.team = team
        self.player = player
        self.playerTo = playerTo


def toDiscordChannelName(name: str) -> str:
    """
    Converts a string to a discord channel like name -> all lowercase and no spaces
    :param name:
    :return:
    """
    if name == None:
        return None
    return name.lower().replace(" ", "-")
    pass

async def createChannel(server: Server, channelName: str):
    """
    Creates a channel on the discord server.
    :param server: Server object --> relevant server for the channel
    :param channelName: Name of the channel that is to be created
    """
    for i in client.get_all_channels():
        if i.name == toDiscordChannelName(channelName) and i.server == server:
            logger.info(f"Channel {channelName} already available ")
            return
    logger.info(f"Creating channel {channelName} on {server.name}")
    await client.create_channel(server, channelName)


async def deleteChannel(server: Server, channelName: str):
    """
    Deletes a channel on the discord server
    :param server: Server object --> relevant server for the channel
    :param channelName: Name of the channel that is to be deleted
    """
    for i in client.get_all_channels():
        if i.name == toDiscordChannelName(channelName) and i.server == server:
            logger.info(f"Deleting channel {toDiscordChannelName(channelName)} on {server.name}")
            await client.delete_channel(i)
            break

schedulerInitRunning = asyncio.Event(loop=client.loop)


async def runScheduler():
    """
    Starts the scheduler task, which will automatically create channels adnd update databases. Currently this is
    always done at 24:00 UTC. Should be called via create_task!
    """
    await client.wait_until_ready()
    while True:
        # take synchronization object, during update no live thread should run!
        schedulerInitRunning.set()
        targetTime = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) + timedelta(days=1)
        logger.info("Initializing schedule for tomorrow")

        # update competitions, seasons etc. Essentially the data that is always there
        updateOverlayData()
        # update all matches for the monitored competitions
        updateMatches()

        # update schedulers that create and delete channels
        client.loop.create_task(updateMatchScheduler())
        schedulerInitRunning.clear()

        await asyncio.sleep(calculateSleepTime(targetTime))


async def runLiveThreader():
    """
    Starts the LiveThreader task, that automatically posts updates from matches to its according matches.
    :todo: remove matches from runningMatches
    :return:
    """
    await client.wait_until_ready()
    runningMatches = []

    while True:
        schedulerInitRunning.wait()

        # Get all matches that are nearly upcoming or currently running
        matchList = [i for i in getCurrentMatches() if i not in runningMatches]
        for match in matchList:
            logger.info(f"Starting match between {match.home_team.clear_name} and {match.away_team.clear_name}")

            client.loop.create_task(runMatchThread(match))
            runningMatches.append(match)
        await asyncio.sleep(60)


async def updateMatchScheduler():
    """
    Creates tasks that create and delete channels at specific times.
    """
    logger.info("Updating match schedule")
    for i in getNextMatchDayObjects():
        client.loop.create_task(asyncCreateChannel(calculateSleepTime(i.startTime), i.matchdayString))
        client.loop.create_task(asyncDeleteChannel(calculateSleepTime(i.endTime), i.matchdayString))
    logger.info("End update schedule")


def calculateSleepTime(targetTime: datetime, nowTime: datetime = datetime.now(timezone.utc)):
    """
    Calculates time between targetTime and nowTime in seconds
    """
    return (targetTime - nowTime).total_seconds()


async def asyncCreateChannel(sleepPeriod: float, channelName: str):
    """
    Async wrapper to create channel
    :param sleepPeriod: Period to wait before channel can be created
    :param channelName: Name of the channel that will be created
    """
    logger.info(f"Initializing create Channel task for {channelName} in {sleepPeriod}")
    await asyncio.sleep(sleepPeriod)
    await createChannel(list(client.servers)[0], channelName)


async def asyncDeleteChannel(sleepPeriod: float, channelName: str):
    """
    Async wrapper to delete channel
    :param sleepPeriod: Period to wait before channel can be deleted
    :param channelName: Name of the channel that will be deleted
    """
    logger.info(f"Initializing delete Channel task for {channelName} in {sleepPeriod}")
    await asyncio.sleep(sleepPeriod)
    await deleteChannel(list(client.servers)[0], channelName)

def getEventIcons(event : MatchEvents) ->str:
    """
    Returns a specific icon for a specific event
    :param event: event that happened
    :return: Stringcode for icon
    """
    if event == MatchEvents.goal:
        return "https://i.imgur.com/pDheMQF.png"
    elif event == MatchEvents.kickoffFirstHalf:
        return "https://i.imgur.com/ABv0qHb.png"
    elif event == MatchEvents.kickoffSecondHalf:
        return "https://i.imgur.com/KfbwJ6f.png"
    elif event == MatchEvents.firstHalfEnd:
        return "https://i.imgur.com/QhAqT4Y.png"
    elif event == MatchEvents.secondHalfEnd:
        return "https://i.imgur.com/7UHsJA8.png"
    elif event == MatchEvents.matchOver:
        return ""
    elif event == MatchEvents.yellowCard:
        return "https://i.imgur.com/lpM48of.png"
    elif event == MatchEvents.redCard:
        return "https://i.imgur.com/mJ9vyRh.png"
    elif event == MatchEvents.substitution:
        return "https://i.imgur.com/xSuf6nq.png"
    elif event == MatchEvents.missedPenalty:
        return "https://i.imgur.com/1fY7wsN.png"
    elif event == MatchEvents.scoredPenalty:
        return "https://i.imgur.com/Q0uncrb.png"
    elif event == MatchEvents.ownGoal:
        return "https://i.imgur.com/1vxHDbr.png"
    else:
        return ""

async def sendMatchEvent(channel: Channel, match: Match, event: MatchEventData):
    """
    This function encapsulates the look and feel of the message that is sent when a matchEvent happens.
    It will build the matchString, the embed object, etc. and than send it to the appropiate channel.
    :param channel: The channel where we want to send things to
    :param match: The match that this message applies to (Metadata!)
    :param event: The actual event that happened. It consists of a MatchEvents enum and a DataDict, which in
    itself contains the minute, team and player(s) the event applies to.
    """

    data = makeMiddlewareCall(DataCalls.liveData + f"/{match.id}")['match']
    homeTeam = match.home_team.clear_name
    awayTeam = match.away_team.clear_name

    if event.event == MatchEvents.goal:
        if event.team == homeTeam:
            goalString = f"[{data['scoreHome']}] : {data['scoreAway']}"
        else:
            goalString = f"{data['scoreHome']} : [{data['scoreAway']}]"
    else:
        goalString = f"{data['scoreHome']} : {data['scoreAway']}"

    title = f"**{homeTeam}** {goalString} **{awayTeam}**"
    content = f"{event.minute}"

    if event.event == MatchEvents.kickoffFirstHalf:
        content += "**KICKOFF** The match is underway!"
    elif event.event == MatchEvents.kickoffSecondHalf:
        content += "**Kickoff** Second Half!"
    elif event.event == MatchEvents.firstHalfEnd:
        content += "**HALF TIME!**"
    elif event.event == MatchEvents.secondHalfEnd:
        content += "Second half has ended."
    elif event.event == MatchEvents.matchOver:
        content += "**FULL TIME**!"
    elif event.event == MatchEvents.goal:
        content += f"**GOAL**! {event.player} scores for **{event.team}**"
    elif event.event == MatchEvents.yellowCard:
        content += f"Yellow card for {event.player}(**{event.team}**)"
    elif event.event == MatchEvents.redCard:
        content += f"Red card for {event.player} (**{event.team}**)"
    elif event.event == MatchEvents.substitution:
        content += f"Substitution **{event.team}**: **{event.player} IN**, ***{event.playerTo} OUT***"
    elif event.event == MatchEvents.missedPenalty:
        content += f"**PENALTY MISSED!** {event.player} has missed a penalty **({event.team})"
    else:
        logger.error(f"Event {event.event} not handled. No message is send to server!")
        return


    embObj = Embed(title=title,description=content)
    url = getEventIcons(event.event)
    if url != "":
        embObj.set_thumbnail(url=getEventIcons(event.event))
    await client.send_message(channel,embed=embObj)


async def runMatchThread(match: Union[str, Match]):
    """
    Start a match threader for a given match. Will read the live data from the middleWare API (data.fifa.com) every
    20 seconds and post the events to the channel that corresponds to the match. This channel has to be created
    previously.
    :param match: Match object.  Will  post to discord channels if the object is a database.models.Match object
    """
    pastEvents = []
    eventList = []

    if isinstance(match, Match):
        matchid = match.id
        channelName = toDiscordChannelName(f"{match.competition.clear_name} Matchday {match.matchday}")
    else:
        raise ValueError("Match needs to be Match instance")
    while True:
        data = makeMiddlewareCall(DataCalls.liveData + f"/{matchid}")

        newEvents, pastEvents = parseEvents(data["match"]["events"], pastEvents)
        eventList += newEvents

        for i in eventList:
            for channel in client.get_all_channels():
                if channel.name == channelName:
                    await sendMatchEvent(channel, match, i)
                    try:
                        eventList.remove(i)
                    except ValueError:
                        pass
                    logger.info(f"Posting event: {i}")

        if data["match"]["isFinished"]:
            logger.info(f"Match {match} finished!")
            break

        await asyncio.sleep(20)


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
        for event in reversed(diff):
            eventData = MatchEventData(event=MatchEvents.none,
                                       minute=event['minute'],
                                       team=event['teamName'],
                                       player=event['playerName'],
                                       playerTo = event['playerToName'],
                                       )
            if event['eventCode'] == 3:  # Goal!
                eventData.event = MatchEvents.goal
            elif event['eventCode'] == 4:  # Substitution!
                eventData.event = MatchEvents.substitution
            elif event['eventCode'] == 1:
                ev = MatchEvents.yellowCard if event['eventDescriptionShort'] == "Y" else MatchEvents.redCard
                eventData.event = ev
            elif event['eventCode'] == 5:
                eventData.event = MatchEvents.missedPenalty
            elif event['eventCode'] == 14:
                ev = MatchEvents.firstHalfEnd if event['phaseDescriptionShort'] == "1H" else MatchEvents.secondHalfEnd
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


async def watchCompetition(competition: Competition, serverName: str):
    """
    Adds a compeitition to be monitored. Also updates matches and competitions accordingly.
    :param competition: Competition to be monitored.
    :param serverName: Name of the discord server
    """
    logger.info(f"Start watching competition {competition} on {serverName}")

    season = None
    while season == None:
        season = Season.objects.filter(competition=competition).order_by('start_date').last()
        if season == None:
            getAndSaveData(getAllSeasons, idCompetitions=competition.id)
    server = DiscordServer(name=serverName)
    server.save()

    updateMatchesSingleCompetition(competition=competition, season=season)

    compWatcher = CompetitionWatcher(competition=competition,
                                     current_season=season, applicable_server=server, current_matchday=1)
    compWatcher.save()
    client.loop.create_task(updateMatchScheduler())

from typing import Dict, Union
import logging
from collections import OrderedDict
from django.core.exceptions import ObjectDoesNotExist
from json.decoder import JSONDecodeError
import subprocess
import sys
import os
import re
from discord import Reaction,User

from database.models import CompetitionWatcher, Competition, MatchEvents, MatchEventIcon,Settings,DiscordUsers
from discord_handler.handler import client, watchCompetition,Scheduler
from discord_handler.cdo_meta import markCommando, CDOInteralResponseData, cmdHandler, emojiList\
    , DiscordCommando,resetPaging,pageNav
from discord_handler.liveMatch import LiveMatch
from api.calls import getLiveMatches,makeMiddlewareCall,DataCalls,getTeamsSearchedByName
from support.helper import shutdown,checkoutVersion,getVersions,currentVersion

from support.helper import Task

logger = logging.getLogger(__name__)

path = os.path.dirname(os.path.realpath(__file__))
"""
Concering commandos: Commandos are automatically added by marking it with the markCommando decorator. This 
decorator also has as a parameter the given Commando that is wished to be used for this Commando. Commandos in 
general get a kwargs object, containing the message and commando, which can be used. It has to return a 
CDOInternalResponseData object, which is used for its response to the channel.

All commandos belong to a certain group and has a certain userlevel associated to it. If no group is explicitly 
associated, it will use the GrpGeneral object as its group. Also, if no userlevel is associated to a commando,
the userlevel of the group is used.
"""


def checkCompetitionParameter(cmdString: str) -> Union[Dict, str]:
    """
    Reads competition parameters, i.e. competition and country code
    :param cmdString: string from message
    :return: Either error message or dict with competition string and country code
    """
    parameterSplit = cmdString.split("#")
    data = parameterSplit[0].split(" ")
    competition_string = ""

    for i in data[1:]:
        if competition_string == "":
            competition_string += i
        else:
            competition_string += " " + i

    logger.debug(f"Competition: {competition_string}, full: {parameterSplit}")

    if len(data) < 2:
        return "Add competition needs the competition as a Parameter!"

    try:
        return {"competition": competition_string, "association": parameterSplit[1]}
    except IndexError:
        return {"competition": competition_string, "association": None}


################################### Commandos ########################################

@markCommando("addCompetition", defaultUserLevel=3)
async def cdoAddCompetition(**kwargs):
    """
    Adds a competition to be watched by soccerbot. It will be regularly checked for new games
    :return: Answer message
    """
    responseData = CDOInteralResponseData()
    parameter = checkCompetitionParameter(kwargs['msg'].content)

    if isinstance(parameter, str):
        responseData.response = "Error within Commando!"
        logger.error("Parameter is not string instance, please check logic!")
        return responseData
    else:
        competition_string = parameter["competition"]
        association = parameter["association"]

    comp = Competition.objects.filter(clear_name=competition_string)

    logger.debug(f"Available competitions: {comp}")

    if len(comp) == 0:
        responseData.response = f"Can't find competition {competition_string}"
        return responseData

    if len(comp) != 1:
        if association == None:
            names = [existing_com.clear_name for existing_com in comp]
            countryCodes = [existing_com.association for existing_com in comp]
            name_code = list(zip(names, countryCodes))
            responseData.response = f"Found competitions {name_code} with that name. Please be more specific (add #ENG for example)."
            return responseData
        else:
            comp = Competition.objects.filter(clear_name=competition_string, association=association)
            if len(comp) != 1:
                names = [existing_com.clear_name for existing_com in comp]
                countryCodes = [existing_com.association for existing_com in comp]
                name_code = list(zip(names, countryCodes))
                responseData.response = f"Found competitions {name_code} with that name. Please be more specific (add #ENG for example)."
                return responseData

    watcher = CompetitionWatcher.objects.filter(competition=comp.first())

    logger.debug(f"Watcher objects: {watcher}")

    if len(watcher) != 0:
        return CDOInteralResponseData(f"Allready watching {competition_string}")

    client.loop.create_task(watchCompetition(comp.first(), kwargs['msg'].server))
    responseData.response = f"Start watching competition {competition_string}"
    return responseData


@markCommando("removeCompetition", defaultUserLevel=3)
async def cdoRemoveCompetition(**kwargs):
    """
    Removes a competition from the watchlist.
    :return: Answer message
    """
    responseData = CDOInteralResponseData()
    parameter = checkCompetitionParameter(kwargs['msg'].content)
    if isinstance(parameter, str):
        return parameter
    else:
        competition_string = parameter["competition"]
        association = parameter["association"]

    watcher = CompetitionWatcher.objects.filter(competition__clear_name=competition_string)

    if len(watcher) == 0:
        responseData.response = f"Competition {competition_string} was not monitored"
        return responseData

    if len(watcher) > 1:
        watcher = watcher.filter(competition__association=association)

    logger.info(f"Deleting {watcher}")
    await Scheduler.removeCompetition(watcher.first())
    watcher.delete()
    responseData.response = f"Removed {competition_string} from monitoring"
    return responseData


@markCommando("monitoredCompetitions")
async def cdoShowMonitoredCompetitions(**kwargs):
    """
    Lists all watched competitions by soccerbot.
    :return: Answer message
    """
    retString = f"Monitored competitions (react with number emojis to remove.Only the first {len(emojiList())} can " \
                f"be added this way):\n\n"
    addInfo = OrderedDict()
    compList = []
    for watchers in CompetitionWatcher.objects.all():
        compList.append(watchers.competition.clear_name)
        try:
            addInfo[watchers.competition.association.clear_name] +=(f"\n{watchers.competition.clear_name}")
        except KeyError:
            addInfo[watchers.competition.association.clear_name] = watchers.competition.clear_name

    def check(reaction, user):
        if reaction.emoji in emojiList():
            index = emojiList().index(reaction.emoji)
            if index < len(compList):
                kwargs['msg'].content = f"!removeCompetition {compList[index]}"
                client.loop.create_task(cmdHandler(kwargs['msg']))
                return True
        return False

    return CDOInteralResponseData(retString, addInfo, check)


@markCommando("listCompetitions")
async def cdoListCompetitionByCountry(**kwargs):
    """
    Lists all competitions for a given country. Needs the name of the country of country code as
    a parameter.
    :return:
    """
    responseData = CDOInteralResponseData()
    data = kwargs['msg'].content.split(" ")

    if len(data) == 0:
        responseData.response = "List competition needs the country or countrycode as parameter"
        return responseData

    association = ""

    for i in data[1:]:
        if association == "":
            association += i
        else:
            association += " " + i

    competition = Competition.objects.filter(association__clear_name=association)

    if len(competition) == 0:
        competition = Competition.objects.filter(association_id=association)

    if len(competition) == 0:
        responseData.response = f"No competitions were found for {association}"
        return responseData

    retString = "Competitions:\n\n"
    compList = []

    for comp in competition:
        retString += comp.clear_name + "\n"
        compList.append(f"{comp.clear_name}#{comp.association.id}")

    retString += f"\n\nReact with according number emoji to add competitions. Only the first {len(emojiList())} can " \
                 f"be added this way"
    responseData.response = retString

    def check(reaction, user):
        if reaction.emoji in emojiList():
            try:
                index = emojiList().index(reaction.emoji)
            except ValueError:
                logger.error(f"{reaction.emoji} not in list!")
                return False
            if index < len(compList):
                kwargs['msg'].content = f"!addCompetition {compList[index]}"
                client.loop.create_task(cmdHandler(kwargs['msg']))
                return True
        return False

    responseData.reactionFunc = check
    return responseData


@markCommando("help")
async def cdoGetHelp(**kwargs):
    """
    Returns all available Commandos and their documentation.
    :return:
    """
    retString = "Available Commandos:"
    addInfo = OrderedDict()
    try:
        prefix = Settings.objects.get(name="prefix")
        prefix = prefix.value
    except ObjectDoesNotExist:
        prefix = "!"

    try:
        userQuery = DiscordUsers.objects.get(id=kwargs['msg'].author.id)
        authorUserLevel = userQuery.userLevel
    except ObjectDoesNotExist:
        authorUserLevel = 0

    addInfoList = []
    count = 0
    for i in DiscordCommando.allCommandos():
        if i.userLevel <= authorUserLevel:
            doc = i.docstring
            doc = re.sub(':.+\n', "", doc)
            doc = re.sub('\n+', "", doc)
            if authorUserLevel >= 5:
                level = f" lvl:{i.userLevel}"
            else:
                level = ""
            addInfo[prefix + i.commando + level] = doc
            count +=1
            if count ==5:
                addInfoList.append(addInfo)
                addInfo = OrderedDict()
                count = 0

    if addInfo != OrderedDict():
        addInfoList.append(addInfo)

    responseData = CDOInteralResponseData(retString, addInfoList[0])

    class pageContent:
        index = 0
        @staticmethod
        def page(page):
            oldIndex = pageContent.index
            pageString = retString + f" _(page {pageContent.index+1})_"
            if page == pageNav.forward:
                pageContent.index +=1
            else:
                pageContent.index -=1
            try:
                return CDOInteralResponseData(pageString,addInfoList[pageContent.index])
            except IndexError:
                pageContent.index = oldIndex
                return CDOInteralResponseData(pageString, addInfoList[pageContent.index])


    responseData.paging = pageContent.page

    return responseData

@markCommando("showRunningTasks", defaultUserLevel=6)
async def cdoShowRunningTasks(**kwargs):
    """
    Shows all currently running tasks on the server
    :return:
    """
    tasks = Task.getAllTaks()
    responseString = "Running tasks:"
    addInfo = OrderedDict()

    for i in tasks:
        args = str(i.args).replace("<", "").replace(">", "").replace(",)", ")")
        addInfo[f"{i.name}{args}"] = f"Started at {i.time}"

    return CDOInteralResponseData(responseString, addInfo)

@markCommando("scores")
async def cdoScores(**kwargs):
    """
    Returns the scores for a given competition/matchday/team
    :param kwargs:
    :return:
    """
    data = kwargs['msg'].content.split(" ")
    channel = kwargs['msg'].channel

    if len(data) == 1:
        if not "-matchday-" in channel.name:
            return CDOInteralResponseData("!scores with no argument can only be called within matchday channels")

        comp,md = Scheduler.findCompetitionMatchdayByChannel(channel.name)

        matchList = Scheduler.getScores(comp,md)

        resp = CDOInteralResponseData("Current scores:")
        addInfo = OrderedDict()
        for matchString,goalList in matchList.items():
            addInfo[matchString] = ""
            for goals in goalList:
                if goals != '':
                    addInfo[matchString] += goals+"\n"
            if addInfo[matchString] == "":
                del addInfo[matchString]
            if addInfo == OrderedDict():
                addInfo[matchString] = "No goals currently."

        if addInfo == OrderedDict():
            resp.response = "Currently no running matches"
        else:
            resp.response = "Current scores:"
        resp.additionalInfo = addInfo
        return resp
    else:
        searchString = kwargs['msg'].content.replace(data[0] + " ","")
        query = Competition.objects.filter(clear_name = searchString)

        if len(query) == 0:
            teamList = getTeamsSearchedByName(searchString)
            if len(teamList) == 0:
                return CDOInteralResponseData(f"Can't find team {searchString}")
            matchObj = teamList[0]['Name'][0]['Description']
            matchList = getLiveMatches(teamID=int(teamList[0]["IdTeam"]))

        else:
            comp = query.first()
            matchObj = comp.clear_name
            matchList = getLiveMatches(competitionID=comp.id)

        if len(matchList) == 0:
            return CDOInteralResponseData(f"No current matches for {matchObj}")

        addInfo = OrderedDict()
        for matchID in matchList:
            try:
                data = makeMiddlewareCall(DataCalls.liveData + f"/{matchID}")
            except JSONDecodeError:
                logger.error(f"Failed to do a middleware call for {matchID}")
                continue

            newEvents, _ = LiveMatch.parseEvents(data["match"]["events"], [])

            class Match:
                id = matchID

            for event in newEvents:
                title,_,goalListing = await LiveMatch.beautifyEvent(event,Match)

                if goalListing != "":
                    try:
                        addInfo[title]+=goalListing + "\n"
                    except KeyError:
                        addInfo[title] = goalListing + "\n"

        if addInfo == OrderedDict():
            return CDOInteralResponseData(f"No goals currently for {matchObj}")

        resp = CDOInteralResponseData(f"Current scores for {matchObj}")
        resp.additionalInfo = addInfo
        return resp

@markCommando("currentGames")
async def cdoCurrentGames(**kwargs):
    """
    Lists all current games within a matchday channel
    :param kwargs:
    :return:
    """
    matchList = Scheduler.startedMatches()
    addInfo = OrderedDict()
    addInfoList = []
    count = 0
    for match in matchList:
        addInfo[match.title] = f"{match.match.date} (UTC)"
        count +=1
        if count == 10:
            addInfoList.append(addInfo)
            addInfo = OrderedDict()
            count = 0

    if addInfo != OrderedDict():
        addInfoList.append(addInfo)

    if addInfo == OrderedDict():
        respStr = "No running matches"
    else:
        respStr = "Running matches:"

    resp = CDOInteralResponseData(respStr)
    if addInfoList != []:
        resp.additionalInfo = addInfo[0]

    class pageContent:
        index = 0
        @staticmethod
        def page(page):
            oldIndex = pageContent.index
            pageString = respStr + f" _(page {pageContent.index+1})_"
            if page == pageNav.forward:
                pageContent.index +=1
            else:
                pageContent.index -=1
            try:
                return CDOInteralResponseData(pageString,addInfoList[pageContent.index])
            except IndexError:
                pageContent.index = oldIndex
                return CDOInteralResponseData(pageString, addInfoList[pageContent.index])

    if addInfoList != []:
        resp.paging = pageContent.page

    return resp

@markCommando("upcomingGames")
async def cdoUpcomingGames(**kwargs):
    """
    Lists all upcoming games
    :param kwargs:
    :return:
    """

    matchList = Scheduler.upcomingMatches()
    addInfo = OrderedDict()
    count = 0
    addInfoList = []
    for match in matchList:
        addInfo[match.title] = f"{match.match.date} (UTC)"
        count +=1
        if count == 10:
            addInfoList.append(addInfo)
            addInfo = OrderedDict()
            count = 0

    if addInfo != OrderedDict():
        addInfoList.append(addInfo)

    if addInfoList == []:
        respStr = "No upcoming matches"
    else:
        respStr = "Upcoming matches:"

    resp = CDOInteralResponseData(respStr)
    if addInfoList != []:
        resp.additionalInfo = addInfoList[0]


    class pageContent:
        index = 0
        @staticmethod
        def page(page):
            oldIndex = pageContent.index
            pageString = respStr + f" _(page {pageContent.index+1})_"
            if page == pageNav.forward:
                pageContent.index +=1
            else:
                pageContent.index -=1
            try:
                return CDOInteralResponseData(pageString,addInfoList[pageContent.index])
            except IndexError:
                pageContent.index = oldIndex
                return CDOInteralResponseData(pageString, addInfoList[pageContent.index])

    if addInfoList != []:
        resp.paging = pageContent.page
    return resp

@markCommando("setStartCommando", defaultUserLevel=5)
async def cdoSetStartCDO(**kwargs):
    """
    Sets a commandline argument to start the bot.
    :param kwargs:
    :return:
    """
    data = kwargs['msg'].content.split(" ")
    if len(data) == 0:
        return CDOInteralResponseData("You need to set a command to be executed to start the bot")

    commandString = kwargs['msg'].content.replace(data[0] + " ", "")

    obj = Settings(name="startCommando",value=commandString)
    obj.save()
    return CDOInteralResponseData(f"Setting startup command to {commandString}")

@markCommando("updateBot", defaultUserLevel=5)
async def cdoUpdateBot(**kwargs):
    """
    Updates bot
    :param kwargs:
    :return:
    """
    def spawnAndWait(listObj):
        p = subprocess.Popen(listObj)
        p.wait()
    data = kwargs['msg'.split(" ")]
    if len(data) != 2:
        return CDOInteralResponseData("Exactly one parameter is allowed. Pass the version or master")

    if data[1] != "master" and data[1] not in getVersions():
        return CDOInteralResponseData(f"Version {data[1]} not available")

    checkoutVersion(data[1])
    spawnAndWait([sys.executable, path + "/../manage.py", "migrate"])
    spawnAndWait([sys.executable, "-m", "pip", "install", "-r", f"{path}/../requirements.txt"])

    return CDOInteralResponseData(f"Updated Bot to {data[1]}. Please restart to apply changes")

@markCommando("stopBot", defaultUserLevel=5)
async def cdoStopBot(**kwargs):
    """
    Stops the execution of the bot
    :param kwargs:
    :return:
    """
    responseData = CDOInteralResponseData()
    retString = f"To confirm the shutdown, please react with {emojiList()[0]} to this message."
    responseData.response = retString

    def check(reaction, user):
        if reaction.emoji == emojiList()[0]:
            client.loop.create_task(client.send_message(kwargs['msg'].channel, "Bot is shutting down in 10 seconds"))
            client.loop.create_task(shutdown())
            return True
        return False

    responseData.reactionFunc = check
    return responseData

@markCommando("restartBot", defaultUserLevel=5)
async def cdoRestartBot(**kwargs):
    """
    Restart Kommando
    :param kwargs:
    :return:
    """
    try:
        Settings.objects.get(name="startCommando")
        logger.info(f"Command: {sys.executable} {path+'/../restart.py'}")
        cmdList = [sys.executable,path+"/../restart.py"]
        logger.info(cmdList)
        p = subprocess.Popen(cmdList)
        logger.info(f"ID of subprocess : {p.pid}")
        return CDOInteralResponseData("Shutting down in 10 seconds. Restart will take around 30 seconds")
    except ObjectDoesNotExist:
        return CDOInteralResponseData("You need to set the startup Command with !setStartCommando before this"
                                      "commando is available")

@markCommando("setPrefix", defaultUserLevel=5)
async def cdoSetPrefix(**kwargs):
    """
    Sets the prefix for the commands
    :param kwargs:
    :return:
    """
    data = kwargs['msg'].content.split(" ")
    if len(data) == 0:
        return CDOInteralResponseData("You need to set a command to be executed to start the bot")

    commandString = kwargs['msg'].content.replace(data[0] + " ", "")
    try:
        prefix = Settings.objects.get(name="prefix")
        prefix.value = commandString
    except ObjectDoesNotExist:
        prefix = Settings(name="prefix",value=commandString)

    prefix.save()
    return CDOInteralResponseData(f"New prefix is {prefix.value}")

@markCommando("setUserPermissions", defaultUserLevel=5)
async def cdoSetUserPermissions(**kwargs):
    """
    Sets the userlevel for the mentioned users.
    :param kwargs:
    :return:
    """
    data = list(kwargs['msg'].content.split(" "))

    if len(kwargs['msg'].mentions) == 0:
        return CDOInteralResponseData("You need to mention a user to set its permission levels")

    for i in data:
        if i.startswith("<@"):
            del data[data.index(i)]

    if len(data) != 2:
        return CDOInteralResponseData("Wrong number of parameters. Needs !setUserPermissions *mentions* userLevel")

    try:
        userLevel = int(data[1])
    except ValueError:
        return CDOInteralResponseData("Userlevel needs to be a number between 0 and 5")

    if userLevel > 5 or userLevel < 0:
        return CDOInteralResponseData("Only user levels from 0 to 5 are available")

    retString = ""
    for user in kwargs['msg'].mentions:
        DiscordUsers(id=user.id,name=user.name,userLevel=userLevel).save()
        retString += f"Setting {user.name} with id {user.id} to user level {userLevel}\n"

    return CDOInteralResponseData(retString)

@markCommando("getUserPermissions",defaultUserLevel=5)
async def cdoGetUserPermissions(**kwargs):
    """
    Gets the userlevel of a mentioned user
    :param kwargs:
    :return:
    """

    data = list(kwargs['msg'].content.split(" "))

    if len(kwargs['msg'].mentions) == 0:
        return CDOInteralResponseData("You need to mention a user to set its permission levels")

    for i in data:
        if i.startswith("<@"):
            del data[data.index(i)]

    if len(data) != 1:
        return CDOInteralResponseData("Wrong number of parameters. Needs !getUserPermissions *mentions* ")

    addInfo = OrderedDict()
    for user in kwargs['msg'].mentions:
        try:
            user = DiscordUsers.objects.get(id=user.id)
            addInfo[user.name] = f"User level: {user.userLevel}"
        except ObjectDoesNotExist:
            addInfo[user.name] = f"User level: 0"

    retObj = CDOInteralResponseData("UserLevels:")
    retObj.additionalInfo = addInfo
    return retObj

@markCommando("versions",defaultUserLevel=5)
async def cdoVersions(**kwargs):
    """
    Shows all available versions for the bot
    :param kwargs:
    :return:
    """
    retString = ""
    for i in getVersions():
        retString += f"Version: **{i}**\n"

    return CDOInteralResponseData(retString)

@markCommando("about",defaultUserLevel=0)
async def cdoAbout(**kwargs):
    """
    About the bot
    :param kwargs:
    :return:
    """
    retstring = "**Soccerbot - a live threading experience**\n\n"
    retstring += f"Current version: {currentVersion()}\n"
    retstring += f"State: good"
    return CDOInteralResponseData(retstring)

@markCommando("log",defaultUserLevel=6)
async def cdoLog(**kwargs):
    """
    Posts the last lines of a given logfile
    :param kwargs:
    :return:
    """

    fileList = ["debug","info","errors"]
    data = kwargs['msg'].content.split(" ")
    if len(data) != 2:
        return CDOInteralResponseData("Data needs to contain logname and length")

    if data[1] not in fileList:
        return CDOInteralResponseData(f"Possible logfiles are {fileList}")

    with open(data[1]+".log") as f:
        fileContent = f.read()

    respStr = "LogContent: "

    addInfo = OrderedDict()
    try:
        addInfo[f"Lines 1 to 1000"] = fileContent[0:200]
    except IndexError:
        addInfo[f"Lines 1 to {len(fileContent)}"] = fileContent[0:len(fileContent) -1]

    class pageContent:
        index = 1
        @staticmethod
        def page(page):
            oldIndex = pageContent.index
            pageString = respStr + f" _(page {pageContent.index+1})_"
            if page == pageNav.forward:
                pageContent.index +=1
            else:
                pageContent.index -=1

            lowerIndex = (pageContent.index - 1) * 1000
            upperIndex = (pageContent.index) * 1000
            addInfo = OrderedDict()
            try:
                addInfo[f"Lines {lowerIndex} to {upperIndex}"] = fileContent[lowerIndex:upperIndex]
            except IndexError:
                addInfo[f"Lines {lowerIndex} to {len(fileContent)}"] = fileContent[lowerIndex:len(fileContent) - 1]
            try:
                return CDOInteralResponseData(pageString,addInfo)
            except IndexError:
                pageContent.index = oldIndex
                return CDOInteralResponseData(pageString, addInfo)

    response = CDOInteralResponseData(respStr,addInfo)
    if len(fileContent) != 0:
        response.paging = pageContent.page

    return response



@markCommando("test", defaultUserLevel=6)
async def cdoTest(**kwargs):
    """
    Test Kommando
    :param kwargs:
    :return:
    """
    msg = await client.send_message(kwargs['msg'].channel, 'React <:yellow_card:478130458090012672> with thumbs up or thumbs down.')
    await client.add_reaction(message=msg, emoji='⏪')
    await client.add_reaction(message=msg,emoji='⏩')

    def check(reaction : Reaction, user : User):
        print(reaction.count)
        if reaction.count == 2:
            client.loop.create_task(resetPaging(reaction.message))
        e = str(reaction.emoji)
        print(e)
        print(e == emojiList()[0])
        return False

    res = await client.wait_for_reaction(message=msg, check=check)
    await client.send_message(kwargs['msg'].channel, '{0.user} reacted with {0.reaction.emoji}!'.format(res))

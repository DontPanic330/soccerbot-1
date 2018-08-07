from django.db import models


class Federation(models.Model):
    id = models.CharField(primary_key=True, max_length=255, verbose_name="Id of Federation, according to API")
    clear_name = models.CharField(max_length=255, verbose_name="Full name of the federation")

    def __str__(self):
        return f"ID: {self.id}, Clear_Name: {self.clear_name}"


class Competition(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="Id of the competitions, according to API")
    federation = models.ForeignKey(Federation, on_delete=models.CASCADE, verbose_name="Federation ID")
    clear_name = models.CharField(max_length=255, verbose_name="Full name of the competition")
    association = models.CharField(max_length=255,verbose_name="Country Code of competition",default="")

    def __str__(self):
        return f"ID: {self.id}, Clear_Name: {self.clear_name}"


class Season(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="Id of the Seasons, according to API")
    federation = models.ForeignKey(Federation, on_delete=models.CASCADE, verbose_name="Federation ID")
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, verbose_name="Competitions ID")
    clear_name = models.CharField(max_length=255, verbose_name="Full name of the given season")
    start_date = models.DateTimeField(verbose_name="S tarting date of the season")
    end_date = models.DateTimeField(verbose_name="End date of the season")

    def __str__(self):
        return f"ID: {self.id}, Clear_Name: {self.clear_name}"


class Team(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="Id of the Team, acording to API")
    clear_name = models.CharField(max_length=255, verbose_name="Full name of the team")
    short_name = models.CharField(max_length=50, verbose_name="Short name of the team",null=True)

    def __str__(self):
        return f"ID: {self.id}, Clear_Name: {self.clear_name}"


class Match(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="Id of the match according to API")
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, verbose_name="Competition ID")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, verbose_name="Seasons ID")
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, verbose_name="Home Team ID",
                                  related_name="home_team", null=True)
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, verbose_name="Away team ID",
                                  related_name="away_team", null=True)
    matchday = models.IntegerField(verbose_name="Matchday count for given league", null=True)
    date = models.DateTimeField(verbose_name="Date of the match")
    score_home_team = models.IntegerField(verbose_name="Score for the home team", null=True)
    score_away_team = models.IntegerField(verbose_name="Score for the away team", null=True)
    passed = models.BooleanField(verbose_name="Flag if the match is allready passed", default=False)

    def __str__(self):
        return f"ID: {self.id}, HomeTeam: {self.home_team_id}, AwayTeam: {self.away_team_id}, matchday: {self.matchday}"

class DiscordServer(models.Model):
    name = models.CharField(max_length=255,verbose_name="Name of the discord server")


class CompetitionWatcher(models.Model):
    competition = models.ForeignKey(Competition, verbose_name="Id of the competition",on_delete=models.CASCADE)
    current_season = models.ForeignKey(Season, verbose_name="Id of the current season",on_delete=models.CASCADE)
    applicable_server = models.ForeignKey(DiscordServer, verbose_name="Id of the discord server",on_delete=models.CASCADE)
    current_matchday = models.IntegerField(default=1,
                                           verbose_name="Current matchday of a given competition with season")


    def __str__(self):
        return f"Competition: {self.competition.clear_name}, Season {self.current_season.clear_name}" \
               f", Matchday {self.current_matchday}, Applicable server: {self.applicable_server.name}"
<!-- ![SecSoc Logo Banner](images/secsoclogobanner.png) -->
# UNSW Discord Verification Bot
<img src="images/email_popup.png" width="300" height="200" alt="Alt text description">
<img src="images/verification_logs.png" width="350" height="200" alt="Alt text description">
<img src="images/verified_role_added.png" width="200" height="250" alt="Alt text description">


This is a simple and secure email-based verification bot for UNSW students and staff, hosted for free by the UNSW Security Society.
It is designed to be as simple as possible to implement in any UNSW society as quickly as possible.
See images of the verification process in `images` folder.

## Setup
1. Create a server role called `verified` which grants users access to server channels.
2. Create a text channel called `verification-logs` 
3. Create a `verify` text channel
4. Invite the discord bot to the server and grant requested permissions [secso.cc/verificationbot](http://secso.cc/verificationbot)
5. Profit!?

### Important notes:
The `verification-logs` channel should only be accessible by a few trusted members of the society's executive team to protect user privacy.
The `verify` channel should be the only channel accessible to an unverified discord user. It doesn't need to be called 'verify'. Also, remove all permissions besides `Use Application Commands` for `@everyone`.

### Backups
SecSoc does not guarantee the availability of backups for all societies so we reccommend regularly utilising the /export (admin only) command and maintain backups for your own society. If issues arise, contact `projects@unswsecurity.com` or for general problems, raise an issue on this GitHub repository.

### Migration
In order to migrate existing verified discord members to this verification bot, you will need to construct an SQLite3 database with columns `discord_id` as the primary key and `email` as the minimum requirement. Optionally, you may also add a `verified` column which stores values 1 or 0 indicating whether this person is actively verified on the server and also a `verified_at` column which stores time of verification in unix time. If `verified` is not specified, the column is created and its value defaults to 1 (meaning verified) and if `verified_at` is omitted then it is left as `NULL`.
You may find it useful to utilise a Python script to migrate your current configuration into a valid database and feel free to contact `projects@unswsecurity.com` for help.

## Why this bot
At the time of writing, this bot provides many benefits over other bots with the same aim:
- No passwords are ever transmitted or stored
- Open source implementation
- Supported by a UNSW SecSoc as compared to individual developers who may stop maintaining their projects
- Ability for societies to manage their own backups
- No dependencies on other small projects
- Verification emails don't get marked as junk mail by UNSW
- Easily self-hostable via [Docker Hub image](https://hub.docker.com/repository/docker/unswsecsoc/unsw-discord-verification-bot)
- Easy to migrate from existing verification solutions
- Support from SecSoc Projects team if issues arise
- Extremely fast setup
- Highly documented for developers

## Contributing
Any PRs, suggestions or issues raised will be attended to by the UNSW SecSoc Projects team. 
Feel free to create a pull request to fix typos, add features or improve performance if you wish and they will be reviewed and accepted if they are likely to prove useful for all societies.
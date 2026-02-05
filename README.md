# UNSW-Discord-Verification-Bot
This is a simple and secure email-based verification bot for UNSW students and staff, hosted for free by the UNSW Security Society.
It is designed to be as simple as possible to implement in any UNSW society as quickly as possible.

<add sreenshots of the verification process here (from user pov)>

## Setup
1. Create a server role called `verified` which grants users access to server channels.
2. Create a text channel called `verification-logs` 
3. Create a `verify` text channel
4. Invite the discord bot to the server and grant requested permissions [<link here>]
5. Profit!?

### Important notes:
The `verification-logs` channel should only be accessible by a few trusted members of the society's executive team to protect user privacy.
The `verify` channel should be the only channel accessible to an unverified discord user. It doesn't need to be called 'verify'.
^-- Utilise Discord roles to implement these.

## Contributing
Any PRs, suggestions or issues raised will be attended to by the UNSW SecSoc Projects team. 
Feel free to create a pull request to fix typos, add features or improve performance if you wish and they will be reviewed and accepted if they are likely to prove useful for all societies.
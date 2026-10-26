# Data Accessibility Guide

Updated: Oct 26, 2026

This document details when and how data that you explicitly enter is
accessible to others.

## Account and contact information

Player email addresses and phone numbers are not displayed on public
profiles. Passwords and OAuth credentials are not published.

A team's **public contact email** is visible to anyone on its profile.
The team account and its managers can add, change or remove this address in Edit profile.
Creating a new team with Google does not automatically publish the Google
email address. Existing team contact addresses remain visible until the
team account or a manager changes or removes them.

## Team managers

Only a team account can appoint or remove its managers. Managers are player
accounts with access to the team's profile, registrations, rosters, and private
team notes. If the team organizes an event or league, its managers also have
organizer access to that event or league and the information available to its
organizers.

Manager access does not expose the team account's password or OAuth credentials.
It does not grant access to a player's private profile information beyond the
permissions held for a particular event or match. Removing a manager ends the
access granted through the team; separately assigned roles still apply.

## Registration forms

An event or league's form requirement and responder link are public. The
response spreadsheet link, tab, and registration code heading are visible only to its
authorized organizers. Arctos checks the configured registration code column and
returns registration eligibility without publishing spreadsheet rows.
Each signed-in player opens their own personalized form link. The configuration's
private signing key remains on the server. The organizer controls access to the
form responses in Google Sheets.

## Injuries
 
New injuries are private by default. Injuries set to **Public** are visible
to everyone on the player's profile. **Private** injuries are hidden from
the public profile; the player can still view and edit them.

Active injuries, including private ones, are shown to authorized match
referees during match setup at events the player is registered for. Marking
an injury healed removes it from this active-injury information. The visibility setting on an existing injury changes only when it is edited.

In either case, injury (description, date, and status) may be
published anonymously. This could be in the form of statistics or
even a full release of information, but no identifying information
nor structure will ever be published (ie, your username will not be
attached, we will never group injuries by user, etc.).

## Penalties

 - are visible to target forever on their profile
 - are visible forever on target's profile to all explicitly listed head
   refs and TOs for the tournament at which they were logged
 - are visible during tournament on target's profile to all head refs for
   the tournament at which they were logged
 - are visible to head refs when starting, running, or viewing a match in 
   the same tournament in which they were logged
 - may be shown in aggregate statistics, but timestamp will be rounded
   to the day, and no information about the author or target will be
   shown besides the target type (team or player)
   
 The [user docs](docs.md#penalties) have more information on the types
 of notes, where they can be seen, and how they get written.

!!! warning "Future Changes"  
    the promises about penalty privacy may change in the future, but
    a) users will be notified of any such changes, and b) they will
    not apply retrospectively (ie, these rules will always apply to
    notes written under these rules)

## Encryption/sysadmin disclaimer

Arctos does not provide end-to-end encryption. Administrators with database or server access can
inspect stored records, including private records. Passwords are stored as
hashes; this does not make other account data inaccessible to administrators.
See the [privacy policy](privacy-policy.md) for how the service handles data.

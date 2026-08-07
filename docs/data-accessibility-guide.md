# Data Accessibility Guide

Updated: Feb 20, 2026

This document details when and how data that you explicitly enter is
accessible to others.

## Never published, in any way

Arctos will never publish, to anyone, your email address, phone number
(if entered), password/oauth secret info.

## Injuries
 
If injuries are set to Public, they are visible to everyone.  If they
are set to Private, then if they are not healed, they are shown to
refs when they start a match you are participating in.

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
   to the day, and no information about author or target will not be
   shown besides the target type (team or player)
   
 The [user docs](docs.md#penalties) have more information on the types
 of notes, where they can be seen, and how they get written.

!!! warning "Future Changes"  
    the promises about penalty privacy may change in the future, but
    a) users will be notified of any such changes, and b) they will
    not apply retrospectively (ie, these rules will always apply to
    notes written under these rules)

## Encryption/sysadmin disclaimer

All of this being said, none of the data dealt with by Arctos is
actually encrypted for server side operations - as described in the
second paragraph of the privacy policy, anyone with server access
could view private information with some minimal technical knowledge
(though authentication data is actually secure). The server itself is
very secure, but you do have to trust the sysadmins to not leak
information (to be clear, they will not).

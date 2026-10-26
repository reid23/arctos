app.services.team_management_service
====================================

Roster changes for a team registered in a standalone event or league.
``change_roster(scope, team_id, registration_id, action, actor=actor)`` requires
the signed-in account as ``actor`` and verifies its permission to manage the team.

``change_roster`` accepts ``add``, ``approve``, ``decline``, or ``remove``. It checks
the player's registration state, registration availability, and roster capacity
for the selected action. Successful changes preserve payment, waiver, and jersey
details and return ``Ok(None)``; rejected actions return ``Err(ArctosError)``.

Approving or declining a pending request verifies the target player's required
form before acquiring the roster write lock. The transaction reloads permissions,
form settings, and the pending registration before confirming it. A change to
the form configuration requires a retry. Adding or removing confirmed players
does not require another form response.

.. automodule:: app.services.team_management_service
   :members:
   :undoc-members:
   :show-inheritance:

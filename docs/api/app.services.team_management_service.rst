app.services.team_management_service
====================================

Roster changes for a team registered in a standalone event or league. The calling
route checks the account's permission to manage the team before invoking the service.

``change_roster`` accepts ``add``, ``approve``, ``decline``, or ``remove``. It checks
the player's registration state, registration availability, and roster capacity
for the selected action. Successful changes preserve payment, waiver, and jersey
details and return ``Ok(None)``; rejected actions return ``Err(ArctosError)``.

.. automodule:: app.services.team_management_service
   :members:
   :undoc-members:
   :show-inheritance:

## Source Resolver

Prefer representing new Home Assistant data needs as typed `EntitySource` models rather
than reaching into Home Assistant clients from EMS or worker code.

Keep source-specific parsing quirks beside the mapper implementation.

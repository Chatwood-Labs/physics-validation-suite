# Integration examples

These examples deliberately use only PVS's generic interfaces. An adapter is a
translation layer, not a place to hide scientific policy.

- `json-existing`: validate already-produced JSON without executing anything.
- `command-python`: execute a small Python program, then validate its JSON.
- `csv-existing`: validate a numeric CSV column.
- `netcdf-command`: execute a program that writes NetCDF, then validate a
  variable.
- `boutpp-command`: a reviewable BOUT++ command/NetCDF wrapper template. It is
  not part of the automated test suite and contains no application-specific physics.
- `python-api`: invoke PVS as a library rather than a subprocess.

The same core code handles every example. There is no `if software ==
"BOUT++"` branch.

All numeric v2 examples declare exact units on the selector, literal operand
and check. Dimensionless quantities use `"1"`; PVS performs no conversion.
The NetCDF examples also select the normative `pvs-netcdf/1` decoding profile.

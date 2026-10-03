; Go: imports, calls, type usage.
(import_spec
  path: (interpreted_string_literal) @import.target)

(import_spec_list
  (import_spec
    path: (interpreted_string_literal) @import.target))

(call_expression
  function: (identifier) @ref.call)

(call_expression
  function: (selector_expression
    field: (field_identifier) @ref.call))

(type_identifier) @ref.type
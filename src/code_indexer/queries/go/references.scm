; Go: imports, calls, type usage.
; The import path is captured a SECOND time as `ref.include` on the same node
; (as the C/C++ queries do) — the `imports` table itself is unchanged.
(import_spec
  path: (interpreted_string_literal) @import.target)

(import_spec
  path: (interpreted_string_literal) @ref.include)

(import_spec_list
  (import_spec
    path: (interpreted_string_literal) @import.target))

(import_spec_list
  (import_spec
    path: (interpreted_string_literal) @ref.include))

(call_expression
  function: (identifier) @ref.call)

(call_expression
  function: (selector_expression
    field: (field_identifier) @ref.call))

(type_identifier) @ref.type

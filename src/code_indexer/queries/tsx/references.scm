; TSX: the TypeScript grammar plus JSX. Same shapes as the TypeScript query
; (this file exists because `_load_query` looks up the language directory by
; name, and `.tsx` files resolve to the `tsx` language). The import target is
; captured a SECOND time as `ref.include` — the `imports` table is unchanged.
(import_statement
  source: (string) @import.target)

(import_statement
  source: (string) @ref.include)

(call_expression
  function: (identifier) @import.require-target
  arguments: (arguments
    (string) @import.require-source))

(call_expression
  function: (identifier)
  arguments: (arguments
    (string) @ref.include))

(call_expression
  function: (identifier) @ref.call)

(call_expression
  function: (member_expression
    property: (property_identifier) @ref.call))

(new_expression
  constructor: (identifier) @ref.call)

(class_heritage
  (extends_clause (identifier) @ref.base_class))
(class_heritage
  (implements_clause (type_identifier) @ref.base_class))

(type_identifier) @ref.type

; TypeScript/JavaScript: imports, requires, calls, type annotations.
(import_statement
  source: (string) @import.target)

(call_expression
  function: (identifier) @import.require-target
  arguments: (arguments
    (string) @import.require-source))

(call_expression
  function: (identifier) @ref.call)

(call_expression
  function: (member_expression
    property: (property_identifier) @ref.call))

(new_expression
  constructor: (identifier) @ref.call)

; class heritage: 'extends Base' / 'implements Iface' (this grammar nests
; extends_clause/implements_clause under class_heritage)
(class_heritage
  (extends_clause (identifier) @ref.base_class))
(class_heritage
  (implements_clause (type_identifier) @ref.base_class))

(type_identifier) @ref.type
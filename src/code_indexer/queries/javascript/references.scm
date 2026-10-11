; JavaScript: imports, requires, calls, class heritage.
; No `type_identifier` node in this grammar (one reason a separate file is
; needed rather than aliasing the TypeScript query). The import/require target
; is captured a SECOND time as `ref.include` on the same node (as the C/C++
; queries do) — the `imports` table itself is unchanged.
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

; class heritage: 'extends Base'. The javascript grammar has no
; extends_clause node (verified by parsing `class A extends B {}`), so the
; base is a direct child of class_heritage.
(class_heritage
  (identifier) @ref.base_class)

(class_heritage
  (member_expression
    property: (property_identifier) @ref.base_class))
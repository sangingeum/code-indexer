; TypeScript/JavaScript: imports, requires, calls, type annotations.
; The import/require target is captured a SECOND time as `ref.include` on the
; same node (as the C/C++ queries do), so the reference store carries an
; `includes` row for it too — the `imports` table itself is unchanged. Unlike
; the C/C++ `#include` node, a require is a CALL, so both require patterns are
; scoped to the `require` function name: an ordinary string-argument call
; (`alert("hi")`) is not an import/include directive (issue #12).
(import_statement
  source: (string) @import.target)

(import_statement
  source: (string) @ref.include)

(call_expression
  function: (identifier) @_fn
  arguments: (arguments
    (string) @import.require-source)
  (#eq? @_fn "require"))

(call_expression
  function: (identifier) @_fn
  arguments: (arguments
    (string) @ref.include)
  (#eq? @_fn "require"))

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

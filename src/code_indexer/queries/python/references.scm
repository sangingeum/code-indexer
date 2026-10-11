; Python: imports and references.
; Import statements -> import rows; calls, decorators, type annotations and
; base classes (argument_list of class_definition) -> reference rows.
; The import/include target is captured a SECOND time as `ref.include` on the
; same node (as the C/C++ queries do), so the reference store carries an
; `includes` row for it as well — the `imports` table itself is unchanged.
(import_statement
  name: (dotted_name) @import.target)

(import_statement
  name: (dotted_name) @ref.include)

(import_statement
  (aliased_import
    name: (dotted_name) @import.target))

(import_statement
  (aliased_import
    name: (dotted_name) @ref.include))

(import_from_statement
  module_name: [(dotted_name) (relative_import)] @import.target)

(import_from_statement
  module_name: [(dotted_name) (relative_import)] @ref.include)

(import_from_statement
  name: (dotted_name) @import.name)

;; call expressions: foo(), obj.method()
(call
  function: (identifier) @ref.call)

(call
  function: (attribute
    attribute: (identifier) @ref.call))

;; decorators reference callables too
(decorator
  (identifier) @ref.call)

(decorator
  (call
    function: (identifier) @ref.call))

;; type positions: annotations
(type
  (identifier) @ref.type)

;; base classes: identifiers inside a class_definition's argument list
(class_definition
  (argument_list
    (identifier) @ref.base_class))

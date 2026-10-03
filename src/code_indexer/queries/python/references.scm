; Python: imports and references.
; Import statements -> import rows; calls, decorators, type annotations and
; base classes (argument_list of class_definition) -> reference rows.
(import_statement
  name: (dotted_name) @import.target)

(import_statement
  (aliased_import
    name: (dotted_name) @import.target))

(import_from_statement
  module_name: [(dotted_name) (relative_import)] @import.target)

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
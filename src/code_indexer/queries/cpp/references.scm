; C++ shares the C shapes with class-base lists added (grammar v15+:
; includes use string_literal, bases use type_descriptor identifiers).
(preproc_include
  path: (string_literal) @import.target)

(preproc_include
  path: (system_lib_string) @import.target)

(preproc_include
  path: (string_literal) @ref.include)

(preproc_include
  path: (system_lib_string) @ref.include)

(call_expression
  function: (identifier) @ref.call)

(call_expression
  function: (field_expression
    field: (field_identifier) @ref.call))

; A template call puts the name one node deeper: `obj.method<T>(...)` and
; `free<T>(...)` parse as template_method / template_function, so the two
; patterns above never see them and a template call site was invisible.
(call_expression
  function: (field_expression
    field: (template_method
      (field_identifier) @ref.call)))

(call_expression
  function: (template_function
    (identifier) @ref.call))

; A call through a qualified name -- `geo::place()`, `Type::make()` -- puts the
; callee one node deeper: the FUNCTION is a qualified_identifier whose `name`
; field is the callee. The scope half stays a namespace_identifier and is
; captured below as a type use; without these two patterns the call half of
; `geo::place()` was invisible, so a project that calls through namespaces
; lost its caller edges. The pattern is anchored on the call's FUNCTION field,
; so a qualified TYPE use (`geo::Point p;`) never reaches it.
(call_expression
  function: (qualified_identifier
    name: (identifier) @ref.call))

(call_expression
  function: (qualified_identifier
    name: (template_function
      (identifier) @ref.call)))

(base_class_clause
  (type_identifier) @ref.base_class)

(type_identifier) @ref.type

; A qualified type name (`TopicEntry::Callback`) puts its qualifier in a
; namespace_identifier, which the plain type_identifier pattern never sees --
; so the type half of `Type::Member` was invisible. Interim `type_use`: the
; index pass keeps it as a `type` reference only when the name binds a
; definition of a TYPE KIND, so `std::` (no definition in the store) and a
; project namespace (`namespace geo`, which is a scope and not a type) both
; add no row. The qualifier's call half is captured above, on the call's
; function field, so `geo::place()` is a `call` row and `geo::Point` stays a
; type use.
(qualified_identifier
  scope: (namespace_identifier) @ref.type_use)
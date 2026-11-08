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

(base_class_clause
  (type_identifier) @ref.base_class)

(type_identifier) @ref.type
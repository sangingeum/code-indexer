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

(base_class_clause
  (type_identifier) @ref.base_class)

(type_identifier) @ref.type
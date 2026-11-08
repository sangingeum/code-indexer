; C: #include directives, calls, type usage in declarations.
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

(type_identifier) @ref.type
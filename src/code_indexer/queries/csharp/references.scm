; C#: using directives, calls, member access, base lists.
;
; The `using_directive` shapes are recorded as imports AND as parallel
; `ref.include` captures on the SAME nodes, so the reference store carries an
; `includes` row for every `using` target (the `imports` table itself is
; unchanged). Namespace-to-file resolution needs project context the indexer
; does not build; never guessed. `using Alias = Estate.Sim;` yields both the
; local alias name and the qualified target, mirroring its `imports` rows.
(using_directive
  (identifier) @import.target)

(using_directive
  (identifier) @ref.include)

(using_directive
  (qualified_name) @import.target)

(using_directive
  (qualified_name) @ref.include)

; Plain calls: Foo(...)
(invocation_expression
  function: (identifier) @ref.call_plain)

; Member access: the whole node is captured so the walk can read BOTH the
; member name and the receiver expression. `a.OughtRoleBindings` is a property
; /field READ captured here — not only invocations — which is the class of
; reference the C# corpus was previously blind to. A member access that is the
; function of an invocation (`ArmorResolution.Classify(...)`) is stored as
; `call`; a plain read is stored as `member`.
(member_access_expression) @ref.member

; Base classes / interfaces in an inheritance list.
(base_list
  (identifier) @ref.base_class)
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
; `call`; a plain read is stored as `member`. Only the OUTERMOST access of a
; chain becomes a row: `System.Console.WriteLine(x)` yields the `WriteLine`
; row and NO row for the intermediate `Console` / `System` segments.
(member_access_expression) @ref.member

; Base classes / interfaces in an inheritance list.
(base_list
  (identifier) @ref.base_class)

; A type named OUTSIDE a member access. A parameter type, a declaration's
; type, a generic argument, an `is` pattern, an `as` expression or a cast
; names a TYPE, and the receiver rule never sees any of them: a type used this
; way produced no row at all. These captures are written as the interim
; `type_use` relationship, exactly like a member-access receiver: the index
; pass turns one into a `type` reference when the name binds a definition of a
; TYPE KIND by the same precedence (this file first, then the store's unique
; definition), keeps it unattributed when the name is ambiguous, and deletes
; it when the store defines the name nowhere or defines it as something that
; is not a type. A VALUE binding is excluded only where the extractor can see
; the binding: a parameter, local, foreach or catch name, a field, a property
; and a generic type parameter are all read off the tree, so a value spelled
; like a type does not become one. A value bound in a form the walk does not
; cover can still look like the type by name alone -- that is the limit, and
; it is stated rather than hidden.
;
; Known limit: only a BARE identifier is captured. `Vivarium.Sim.NeedBand b`
; writes a qualified_name, which none of the patterns below match, so a type
; spelled with its namespace produces no row at all.
(parameter type: (identifier) @ref.type_use)

(variable_declaration type: (identifier) @ref.type_use)

(property_declaration type: (identifier) @ref.type_use)

(method_declaration returns: (identifier) @ref.type_use)

(cast_expression type: (identifier) @ref.type_use)

(as_expression right: (identifier) @ref.type_use)

(declaration_pattern type: (identifier) @ref.type_use)

; `Dictionary<string, NeedBand>`: the argument list is where the project's own
; types sit inside an otherwise external generic.
(type_argument_list (identifier) @ref.type_use)
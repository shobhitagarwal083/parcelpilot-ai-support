import type { Persona } from "../lib/types";

/* The persona switcher is the demo control.
 *
 * Scope and capabilities are shown before a question is ever asked, so the
 * access boundary is legible rather than something you have to take on trust.
 * Switching persona and watching the same question get a different answer is
 * the clearest evidence that enforcement lives in the data layer.
 */

function Group({
  label,
  personas,
  active,
  onPick,
}: {
  label: string;
  personas: Persona[];
  active: string;
  onPick: (id: string) => void;
}) {
  if (!personas.length) return null;
  return (
    <div className="persona-group">
      <div className="rail-section-label">{label}</div>
      {personas.map((persona) => (
        <button
          key={persona.id}
          className="persona"
          aria-pressed={persona.id === active}
          onClick={() => onPick(persona.id)}
        >
          <span className="persona-name">{persona.display_name.replace(/\s*\((customer|internal)\)$/, "")}</span>
          <span className="persona-role">{persona.role}</span>
        </button>
      ))}
    </div>
  );
}

export function PersonaRail({
  personas,
  active,
  onPick,
}: {
  personas: Persona[];
  active: Persona | null;
  onPick: (id: string) => void;
}) {
  const customers = personas.filter((p) => p.kind === "customer");
  const internal = personas.filter((p) => p.kind === "internal");

  return (
    <aside className="rail">
      <div className="rail-brand">
        <h1>ParcelPilot</h1>
        <span>support console</span>
      </div>

      <div>
        <Group
          label="Customers"
          personas={customers}
          active={active?.id ?? ""}
          onPick={onPick}
        />
        <Group label="ParcelPilot staff" personas={internal} active={active?.id ?? ""} onPick={onPick} />
      </div>

      {active && (
        <div className="scope-card">
          <div className="scope-row">
            <span className="scope-key">Sees</span>
            <div className="chips">
              {active.account_ids.length ? (
                active.account_ids.map((id) => (
                  <span className="chip" key={id}>
                    {id}
                  </span>
                ))
              ) : (
                <span className="chip">all accounts</span>
              )}
            </div>
          </div>
          <div className="scope-row">
            <span className="scope-key">Can</span>
            <div className="chips">
              {active.capabilities.length ? (
                active.capabilities.map((capability) => (
                  <span className="chip" key={capability}>
                    {capability}
                  </span>
                ))
              ) : (
                <span className="chip chip-none">nothing</span>
              )}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

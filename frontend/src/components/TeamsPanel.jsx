import { api, usePoll } from "../api.js";

export function TeamsPanel({ onSelectTeam, activeTeam, onSelectCall }) {
  const { data, error } = usePoll(api.teams, 3000);
  const teams = data?.teams ?? [];

  return (
    <section
      className="flex min-h-0 flex-1 flex-col rounded-sm border border-line bg-panel"
      aria-labelledby="teams-heading"
    >
      <div className="flex flex-wrap items-baseline gap-3 border-b border-line px-5 py-3">
        <div>
          <h2
            id="teams-heading"
            className="text-[13px] font-semibold uppercase tracking-[0.12em] text-ink"
          >
            Rescue Teams & Bases
          </h2>
          <p className="font-mono text-[11px] text-muted">
            {data?.total_assigned || 0} victims assigned across {teams.length} rescue bases in Ernakulam
          </p>
        </div>
        {activeTeam && (
          <button
            onClick={() => onSelectTeam(null)}
            className="ml-auto rounded-sm border border-line bg-raised px-2.5 py-1 font-mono text-[11px] text-muted transition-colors hover:text-ink"
          >
            Show all teams
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="p-4 font-mono text-[11px] text-band3">
          Failed to load rescue teams: {error.message}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => {
            const isSelected = activeTeam === team.id;
            return (
              <div
                key={team.id}
                className={`flex flex-col justify-between rounded-md border p-3.5 transition-all ${
                  isSelected
                    ? "border-water bg-water/5 shadow-sm"
                    : "border-line bg-ground hover:border-line/90"
                }`}
              >
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="flex items-center gap-2">
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: team.color }}
                          aria-hidden="true"
                        />
                        <h3 className="text-[14px] font-bold text-ink">{team.name}</h3>
                      </div>
                      <p className="mt-0.5 font-mono text-[10.5px] text-muted">{team.hub}</p>
                    </div>
                    <span
                      className="rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold"
                      style={{
                        backgroundColor: `${team.color}20`,
                        color: team.color,
                      }}
                    >
                      {team.radius_km} km radius
                    </span>
                  </div>

                  <div className="mt-3 grid grid-cols-2 gap-2 border-y border-line/60 py-2.5 font-mono text-[11px]">
                    <div>
                      <span className="text-[10px] uppercase text-muted">Assigned Victims</span>
                      <p className="text-[16px] font-bold text-ink">{team.total_assigned}</p>
                    </div>
                    <div>
                      <span className="text-[10px] uppercase text-muted">Life Threats</span>
                      <p
                        className={`text-[16px] font-bold ${
                          team.life_threats > 0 ? "text-band3" : "text-ok"
                        }`}
                      >
                        {team.life_threats}
                      </p>
                    </div>
                    <div>
                      <span className="text-[10px] uppercase text-muted">Avg Distance</span>
                      <p className="text-[12px] text-ink-2">
                        {team.avg_distance_km ? `${team.avg_distance_km} km` : "—"}
                      </p>
                    </div>
                    <div>
                      <span className="text-[10px] uppercase text-muted">Closest Target</span>
                      <p className="text-[12px] text-water">
                        {team.closest_distance_km != null
                          ? `${team.closest_distance_km} km`
                          : "—"}
                      </p>
                    </div>
                  </div>

                  <div className="mt-2.5">
                    <p className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-muted">
                      Equipment & Vehicles
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {team.vehicles.map((v) => (
                        <span
                          key={v}
                          className="rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-[9.5px] text-ink-2"
                        >
                          {v}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="mt-3.5 pt-2">
                  <button
                    onClick={() => onSelectTeam(isSelected ? null : team.id)}
                    className={`w-full rounded-sm px-3 py-1.5 font-mono text-[11px] font-semibold transition-colors ${
                      isSelected
                        ? "bg-water text-ground"
                        : "border border-line bg-raised text-ink hover:border-water hover:text-water"
                    }`}
                  >
                    {isSelected ? "✓ Active Filter (Viewing Roster)" : "View Team Targets →"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

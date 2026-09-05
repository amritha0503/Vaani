import { useEffect, useRef } from "react";
import { bandClass, bandName, fmtAge } from "../api.js";

const TWIN = "Water is rising, please help.";

function Chip({ children, tone = "", caps = true }) {
  return (
    <span
      className={`rounded-[3px] border border-line px-1.5 py-px font-mono text-[9.5px] tracking-[0.06em] text-muted ${
        caps ? "uppercase" : ""
      } ${tone}`}
    >
      {children}
    </span>
  );
}

/** The terrain reason, with the parts that matter picked out of the sentence. */
function Reason({ text }) {
  const parts = text.split(" · ");
  return (
    <span className="font-mono text-[10.5px] leading-relaxed text-muted">
      {parts.map((p, i) => {
        const tone = /LIFE THREAT|REQUEST LANDMARK/.test(p)
          ? "text-band3"
          : /TERRAIN EDGE|LOCATING|CALL ENDED/.test(p)
            ? "text-band2"
            : /above nearest drainage/.test(p)
              ? "text-water"
              : "";
        return (
          <span key={i} className={tone}>
            {i > 0 && <span className="text-line"> · </span>}
            {p}
          </span>
        );
      })}
    </span>
  );
}

/** The one thing worth proving in thirty seconds: two callers who said the
 *  exact same words, ranked apart because the ground under them is not the
 *  same. Shown whenever the fixture pair (or a real duplicate) lands on the
 *  board -- never fabricated, always the live rank of two real rows. */
function TwinProof({ calls }) {
  const twins = calls.filter((c) => c.transcript === TWIN).sort((a, b) => a.rank - b.rank);
  if (twins.length < 2) return null;
  const [a, b] = twins;
  return (
    <div className="border-b border-line bg-band2/10 px-4 py-2 sm:px-5">
      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-band2">
        Twin-call proof — identical words, different ground
      </p>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 font-mono text-[11px] text-ink-2">
        <span className="text-ink">#{a.rank}</span>
        <span>{a.hand_m != null ? `${a.hand_m.toFixed(1)} m above drainage` : "no fix"} · exposure {a.exposure_p75}</span>
        <span className="text-muted">vs</span>
        <span className="text-ink">#{b.rank}</span>
        <span>{b.hand_m != null ? `${b.hand_m.toFixed(1)} m above drainage` : "no fix"} · exposure {b.exposure_p75}</span>
      </div>
    </div>
  );
}

function Row({ call, selected, onSelect, refFn }) {
  const twin = call.transcript === TWIN;
  const live = call.live_stage && !String(call.live_stage).startsWith("done");
  const working = call.intake_stage || call.extractor_pending;

  return (
    <li>
      <button
        ref={refFn}
        onClick={() => onSelect(call.id)}
        aria-current={selected ? "true" : undefined}
        aria-label={`Position ${call.rank}. ${bandName(call.band)}. ${call.transcript}. ${call.reason}`}
        className={`grid w-full grid-cols-[30px_28px_minmax(0,1fr)] items-start gap-3 border-b border-line-soft px-4 py-2.5 text-left transition-colors sm:px-5 ${
          selected ? "bg-raised" : "hover:bg-panel"
        } ${selected ? "shadow-[inset_3px_0_0_var(--color-water)]" : twin ? "shadow-[inset_3px_0_0_var(--color-band2)]" : ""}`}
      >
        <span
          className={`pt-px text-right font-mono text-[16px] font-semibold tabular-nums ${
            call.rank === 1 ? "text-ink" : "text-muted"
          }`}
        >
          {call.rank}
        </span>

        <span
          className={`mt-0.5 rounded-[3px] border py-0.5 text-center font-mono text-[10px] font-bold ${bandClass(call.band)}`}
          title={bandName(call.band)}
        >
          B{call.band}
        </span>

        <span className="min-w-0">
          <span className="block truncate text-[13.5px] leading-snug text-ink">
            {call.transcript}
          </span>
          <span className="mt-0.5 block">
            <Reason text={call.reason} />
          </span>
          <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {live && (
              <Chip tone="!text-water !border-water/45">
                <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-water pulse align-middle" />
                on call
              </Chip>
            )}
            {call.source === "upload" && <Chip>upload</Chip>}
            {call.place && <Chip>{call.place.name}</Chip>}
            {call.language && call.language !== "?" && <Chip>{call.language}</Chip>}
            <Chip>{call.hazard}</Chip>
            {call.trapped && <Chip tone="!text-band3 !border-band3/40">trapped</Chip>}
            {call.vulnerable.map((v) => (
              <Chip key={v}>{v}</Chip>
            ))}
            <Chip
              tone={
                call.extractor === "keyword"
                  ? "!text-band2 !border-band2/40"
                  : call.extractor === "llm+floor"
                    ? "!text-band3 !border-band3/45"
                    : ""
              }
            >
              {working ? (
                <span className="relative inline-block overflow-hidden sweep">
                  {call.intake_stage ?? "spotter"} · model working
                </span>
              ) : (
                call.extractor
              )}
            </Chip>
            {call.error_radius_m > 0 && <Chip caps={false}>±{call.error_radius_m} m</Chip>}
            <Chip caps={false}>{fmtAge(call.age_s)}</Chip>
          </span>
        </span>
      </button>
    </li>
  );
}

export default function Queue({ calls, selected, onSelect, connected }) {
  const listRef = useRef(null);
  const rowRefs = useRef(new Map());

  useEffect(() => {
    if (!selected) return;
    rowRefs.current.get(selected)?.scrollIntoView({ block: "nearest" });
  }, [selected, calls]);

  const onKeyDown = (e) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const i = calls.findIndex((c) => c.id === selected);
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? calls.length - 1
          : Math.min(
              calls.length - 1,
              Math.max(0, (i === -1 ? 0 : i) + (e.key === "ArrowDown" ? 1 : -1))
            );
    const target = calls[next];
    if (target) {
      onSelect(target.id);
      rowRefs.current.get(target.id)?.focus();
    }
  };

  const threats = calls.filter((c) => c.band >= 3).length;

  return (
    <section
      className="flex min-w-0 flex-1 flex-col lg:min-h-0"
      aria-labelledby="queue-heading"
    >
      <div className="flex items-baseline gap-3 border-b border-line bg-panel px-4 py-2.5 sm:px-5">
        <h2
          id="queue-heading"
          className="text-[12px] font-semibold uppercase tracking-[0.12em] text-ink-2"
        >
          Rescue queue
        </h2>
        <p className="font-mono text-[11px] text-muted">
          {calls.length} call{calls.length === 1 ? "" : "s"}
          {threats > 0 && (
            <>
              {" · "}
              <span className="text-band3">{threats} life threat</span>
            </>
          )}
        </p>
        <p className="ml-auto hidden font-mono text-[10px] text-muted lg:block">
          ↑↓ to move · terrain reorders within a band, never across one
        </p>
      </div>

      <TwinProof calls={calls} />

      {calls.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <p className="text-[13px] text-ink-2">
            {connected ? "No calls on the board." : "Waiting for the queue stream…"}
          </p>
          <p className="max-w-sm font-mono text-[11px] leading-relaxed text-muted">
            Inject the fixture surge, upload recordings from Intake, or take a call.
            Calls appear the moment they connect and fill in as the agent works.
          </p>
        </div>
      ) : (
        <ul
          ref={listRef}
          onKeyDown={onKeyDown}
          className="lg:min-h-0 lg:flex-1 lg:overflow-y-auto"
        >
          {calls.map((c) => (
            <Row
              key={c.id}
              call={c}
              selected={selected === c.id}
              onSelect={onSelect}
              refFn={(el) => {
                if (el) rowRefs.current.set(c.id, el);
                else rowRefs.current.delete(c.id);
              }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

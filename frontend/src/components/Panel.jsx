export function Panel({ title, note, children, className = "" }) {
  return (
    <section
      className={`flex min-h-0 flex-col rounded-sm border border-line bg-panel ${className}`}
    >
      <div className="flex items-baseline gap-3 border-b border-line px-4 py-3">
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        {note && <p className="text-[12.5px] text-muted">{note}</p>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
    </section>
  );
}

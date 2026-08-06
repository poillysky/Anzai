import { ICON_SIZE_EMPTY, Inbox, type LucideIcon } from "@/components/ui/icons";

export function EmptyState({
  title,
  hint,
  icon: Icon = Inbox,
}: {
  title: string;
  hint?: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="empty-state">
      <Icon
        className="empty-state-icon"
        size={ICON_SIZE_EMPTY}
        strokeWidth={1.5}
        absoluteStrokeWidth
        aria-hidden
      />
      <p className="empty-state-title">{title}</p>
      {hint ? <p className="empty-state-hint">{hint}</p> : null}
    </div>
  );
}

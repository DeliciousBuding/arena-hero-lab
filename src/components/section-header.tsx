/** 统一区块头：标题 + 英文副题 + 说明 + 右侧操作区 */
export function SectionHeader({
  id,
  title,
  enTitle,
  description,
  action,
}: {
  id?: string;
  title: string;
  enTitle?: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      id={id}
      className={`mb-5 flex flex-wrap items-end justify-between gap-3 scroll-mt-24 ${
        id ? "scroll-mt-24" : ""
      }`}
    >
      <div>
        <h2 className="flex items-baseline gap-2 text-lg font-semibold text-text-primary">
          {title}
          {enTitle ? <span className="text-xs font-normal text-text-tertiary">{enTitle}</span> : null}
        </h2>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-text-secondary">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

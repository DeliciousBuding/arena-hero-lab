import { cn } from "@/lib/utils";
import { Separator } from "@/components/ui/separator";

/**
 * 统一区块头：serif 标题 + 英文副题 + 说明 + 右侧操作区。
 * 底部 hairline 分隔，与 Anthropic 编辑性版式一致。
 */
export function SectionHeader({
  id,
  title,
  enTitle,
  description,
  action,
  className,
}: {
  id?: string;
  title: string;
  enTitle?: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      id={id}
      className={cn(
        "mb-6 flex flex-wrap items-end justify-between gap-3 scroll-mt-20",
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="flex items-baseline gap-2 font-serif text-xl font-normal leading-tight text-foreground">
          {title}
          {enTitle ? (
            <span className="font-sans text-xs font-normal text-muted-foreground">
              {enTitle}
            </span>
          ) : null}
        </h2>
        {description ? (
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {action}
      <Separator className="basis-full" />
    </div>
  );
}

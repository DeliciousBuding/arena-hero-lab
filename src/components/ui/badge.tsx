import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Badge 原语：标签/状态标记，cva 定义变体约束。
 * 圆角统一 full（药丸），尺寸固定，颜色按语义变体。
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-medium leading-none transition-colors",
  {
    variants: {
      variant: {
        default: "border-border bg-secondary text-secondary-foreground",
        primary: "border-foreground/20 bg-foreground text-background",
        brand: "border-brand/30 bg-brand-soft text-brand",
        gold: "border-rank-gold/40 bg-rank-gold/10 text-rank-gold",
        silver: "border-rank-silver/40 bg-rank-silver/10 text-rank-silver",
        bronze: "border-rank-bronze/40 bg-rank-bronze/10 text-rank-bronze",
        outline: "border-border bg-transparent text-muted-foreground",
        destructive: "border-destructive/30 bg-destructive/10 text-destructive",
        success: "border-success/30 bg-success/10 text-success",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };

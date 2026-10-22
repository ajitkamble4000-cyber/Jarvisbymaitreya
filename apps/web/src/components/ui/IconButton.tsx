import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "./cn";
import { Button, type ButtonVariant } from "./Button";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Accessible name. Required because an icon alone has no text. Also used as the tooltip. */
  label: string;
  icon: ReactNode;
  variant?: ButtonVariant;
  loading?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, variant = "ghost", className, loading, ...rest },
  ref,
) {
  return (
    <Button ref={ref} variant={variant} size="icon" aria-label={label} title={label} loading={loading} className={cn(className)} {...rest}>
      {loading ? null : icon}
    </Button>
  );
});

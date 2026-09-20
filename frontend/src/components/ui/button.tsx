import type { ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "outline" | "danger";

/**
 * Each variant is a complete, self-contained color class set with no background/text-color
 * utility shared across variants (ERP-074) -- Tailwind resolves conflicting utility classes by
 * generated-stylesheet order, not by the order they appear in a class string, so a per-call-site
 * override like `className="bg-white text-red-600"` layered on top of a hardcoded default
 * (e.g. `bg-brand text-white`) could silently lose that fight and render invisible text. Giving
 * each look its own variant removes the conflict entirely instead of patching around it.
 */
const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-brand text-white hover:bg-brand-dark",
  outline: "bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100",
  danger: "bg-white text-red-600 ring-1 ring-red-200 hover:bg-red-50",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 ${VARIANT_CLASSES[variant]} ${className}`}
      {...props}
    />
  );
}

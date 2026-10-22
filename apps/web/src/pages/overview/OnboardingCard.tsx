import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { onboardingComplete, readDismissed, writeDismissed, type OnboardingStep } from "./onboarding";

/** The first-run checklist. Hidden when every step is done or when the viewer has hidden it. */
export function OnboardingCard({ steps }: { steps: readonly OnboardingStep[] }) {
  const [dismissed, setDismissed] = useState(() => readDismissed());
  if (dismissed || onboardingComplete(steps)) return null;
  const done = steps.filter((step) => step.done).length;

  return (
    <Card className="flex flex-col gap-4" aria-labelledby="onboarding-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle id="onboarding-title">Get JARVIS ready</CardTitle>
          <p className="text-sm text-muted" aria-live="polite">
            {done} of {steps.length} steps done.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            writeDismissed(true);
            setDismissed(true);
          }}
        >
          Hide this checklist
        </Button>
      </div>
      <ol className="flex flex-col gap-2">
        {steps.map((step) => (
          <li key={step.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-raised p-3">
            <span className="text-sm text-ink">{step.label}</span>
            {step.done ? (
              <Badge tone="success">Done</Badge>
            ) : (
              <Link to={step.href} className="text-sm font-medium text-accent underline underline-offset-4">
                Do this step
                <span className="sr-only"> ({step.label})</span>
              </Link>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

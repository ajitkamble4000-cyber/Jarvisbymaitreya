import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { fieldErrorsFromIssues, loginErrorMessage, loginSchema, safeReturnPath, type FieldErrors } from "./authForms";

/** Sign-in screen. Returns the viewer to the page they asked for, or to Overview. */
export default function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [values, setValues] = useState({ email: "", password: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  const onSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setFormError(null);
    const parsed = loginSchema.safeParse(values);
    if (!parsed.success) {
      setErrors(fieldErrorsFromIssues(parsed.error.issues));
      return;
    }
    setErrors({});
    try {
      await auth.login.mutateAsync(parsed.data);
      navigate(safeReturnPath(location.state), { replace: true });
    } catch (error) {
      setFormError(loginErrorMessage(error));
      setValues((current) => ({ ...current, password: "" }));
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Sign in" description="Sign in to reach your assistant, devices and tasks." />
      <Card>
        <form className="flex flex-col gap-4" onSubmit={(event) => void onSubmit(event)} noValidate>
          <Field label="Email" error={errors.email}>
            {(control) => (
              <Input {...control} type="email" autoComplete="username" value={values.email} onChange={(event) => setValues((current) => ({ ...current, email: event.target.value }))} required autoFocus />
            )}
          </Field>
          <Field label="Password" error={errors.password}>
            {(control) => (
              <Input
                {...control}
                type="password"
                autoComplete="current-password"
                value={values.password}
                onChange={(event) => setValues((current) => ({ ...current, password: event.target.value }))}
                required
              />
            )}
          </Field>
          {formError ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {formError}
            </p>
          ) : null}
          <Button type="submit" variant="primary" loading={auth.login.isPending} className="w-full">
            Sign in
          </Button>
        </form>
      </Card>
    </div>
  );
}

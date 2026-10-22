import { useState, type ChangeEvent, type FormEvent } from "react";
import { useAuth } from "../../hooks/useAuth";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { MIN_PASSWORD_LENGTH, fieldErrorsFromApiError, fieldErrorsFromIssues, setupSchema, type FieldErrors } from "./authForms";

/** First-run screen. Creates the single owner account. Only shown while setup_required is true. */
export default function SetupPage() {
  const auth = useAuth();
  const [values, setValues] = useState({ email: "", display_name: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  const update = (field: keyof typeof values) => (event: ChangeEvent<HTMLInputElement>) => {
    setValues((current) => ({ ...current, [field]: event.target.value }));
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setFormError(null);
    const parsed = setupSchema.safeParse(values);
    if (!parsed.success) {
      setErrors(fieldErrorsFromIssues(parsed.error.issues));
      return;
    }
    setErrors({});
    try {
      await auth.setup.mutateAsync({
        email: parsed.data.email,
        password: parsed.data.password,
        ...(parsed.data.display_name ? { display_name: parsed.data.display_name } : {}),
      });
    } catch (error) {
      const mapped = fieldErrorsFromApiError(error);
      setErrors(mapped.fields);
      setFormError(mapped.message);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Set up JARVIS" description="Create the owner account for this server. Only one owner can exist, and this screen closes once it is created." />
      <Card>
        <form className="flex flex-col gap-4" onSubmit={(event) => void onSubmit(event)} noValidate>
          <CardTitle className="sr-only">Owner account</CardTitle>
          <Field label="Email" error={errors.email}>
            {(control) => <Input {...control} type="email" autoComplete="username" value={values.email} onChange={update("email")} required autoFocus />}
          </Field>
          <Field label="Display name (optional)" error={errors.display_name}>
            {(control) => <Input {...control} autoComplete="name" value={values.display_name} onChange={update("display_name")} />}
          </Field>
          <Field label="Password" hint={`At least ${MIN_PASSWORD_LENGTH} characters. You will use it to sign in.`} error={errors.password}>
            {(control) => <Input {...control} type="password" autoComplete="new-password" value={values.password} onChange={update("password")} required />}
          </Field>
          <Field label="Confirm password" error={errors.confirm}>
            {(control) => <Input {...control} type="password" autoComplete="new-password" value={values.confirm} onChange={update("confirm")} required />}
          </Field>
          {formError ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {formError}
            </p>
          ) : null}
          <Button type="submit" variant="primary" loading={auth.setup.isPending} className="w-full">
            Create owner account
          </Button>
        </form>
      </Card>
    </div>
  );
}

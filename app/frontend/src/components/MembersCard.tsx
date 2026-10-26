import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { getMembers, inviteMember } from "../api/endpoints";
import { formatDateTime } from "../lib/format";
import { errorMessage } from "./ErrorNotice";
import { Badge, Button, Card, Field, Input, SkeletonRows, TableFrame, Td, Th } from "./ui";

/* The org's members, and the owner's invitation. Only the owner sees it: the
 * API answers 403 to anyone else on /org/members. Inviting creates the
 * member's Cognito user, and Cognito emails it a temporary password that the
 * Hosted UI swaps for a new one on its first login. */
export function MembersCard() {
  const queryClient = useQueryClient();
  const membersQuery = useQuery({ queryKey: ["org", "members"], queryFn: getMembers });
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => inviteMember({ email }),
    onSuccess: (member) => {
      setError(null);
      setMessage(
        member.invitation_sent
          ? `Invitamos a ${member.email}: le llega un email con una contraseña temporal.`
          : `${member.email} ya tenía cuenta: entra con su contraseña y ya está en tu organización.`,
      );
      setEmail("");
      queryClient.invalidateQueries({ queryKey: ["org", "members"] });
    },
    onError: (err) => {
      setMessage(null);
      setError(errorMessage(err));
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  return (
    <Card
      title="Miembros"
      description="Quienes usan el portal con tu organización."
      bodyClassName="p-0"
      footer={
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3 py-2">
          <Field label="Invitar por email" className="min-w-0 flex-1">
            {({ id }) => (
              <Input
                id={id}
                type="email"
                required
                maxLength={254}
                autoComplete="off"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="min-h-11"
              />
            )}
          </Field>
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {mutation.isPending ? "Invitando…" : "Invitar"}
          </Button>
          {message && (
            <p role="status" className="w-full rounded-lg bg-[#eaf7ea] px-3 py-2 text-[15px] text-good-ink">
              {message}
            </p>
          )}
          {error && (
            <p role="alert" className="w-full rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
              {error}
            </p>
          )}
        </form>
      }
    >
      {membersQuery.isLoading ? (
        <div className="p-4 sm:p-5">
          <SkeletonRows rows={2} />
        </div>
      ) : (
        <TableFrame caption="Miembros de la organización" minWidth="28rem">
          <thead>
            <tr>
              <Th>Email</Th>
              <Th>Rol</Th>
              <Th>Último ingreso</Th>
            </tr>
          </thead>
          <tbody>
            {membersQuery.data?.map((member) => (
              <tr key={member.user_id}>
                <Td className="break-all">{member.email}</Td>
                <Td>
                  <Badge tone={member.role === "owner" ? "info" : "neutral"}>
                    {member.role === "owner" ? "Dueño" : "Miembro"}
                  </Badge>
                </Td>
                <Td className="text-ink-2">
                  {member.last_login_at ? formatDateTime(member.last_login_at) : "Todavía no entró"}
                </Td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
      )}
      {membersQuery.isError && (
        <p role="alert" className="px-5 py-3 text-[15px] text-critical">
          {errorMessage(membersQuery.error)}
        </p>
      )}
    </Card>
  );
}

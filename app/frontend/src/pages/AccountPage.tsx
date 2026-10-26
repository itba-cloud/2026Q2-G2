import { useQuery } from "@tanstack/react-query";
import { getMerchant } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { MembersCard } from "../components/MembersCard";
import { MerchantForm } from "../components/MerchantForm";
import { IconUser } from "../components/icons";
import { Badge, Card, Page, PageHeader } from "../components/ui";

export function AccountPage() {
  const { user } = useAuth();
  const merchantQuery = useQuery({ queryKey: ["me", "merchant"], queryFn: getMerchant });

  return (
    <Page
      header={
        <PageHeader
          icon={<IconUser />}
          title="Tu cuenta"
          description={
            user?.role === "owner"
              ? "Los datos de tu usuario, el perfil del comercio y sus miembros."
              : "Los datos de tu usuario y el perfil del comercio."
          }
        />
      }
    >
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
        <Card title="Usuario" scroll>
          <dl className="space-y-3.5">
            <div>
              <dt className="text-[13px] text-muted">Email</dt>
              <dd className="mt-0.5 break-all text-[15px] text-ink">{user?.email}</dd>
            </div>
            <div>
              <dt className="text-[13px] text-muted">Organización</dt>
              <dd className="mt-0.5 text-[15px] text-ink">{user?.org_name}</dd>
            </div>
            <div>
              <dt className="text-[13px] text-muted">Rol</dt>
              <dd className="mt-1">
                <Badge tone={user?.role === "owner" ? "info" : "neutral"}>
                  {user?.role === "owner" ? "Dueño de la cuenta" : "Miembro"}
                </Badge>
              </dd>
            </div>
          </dl>
        </Card>

        {/* Cualquier usuario, no sólo el dueño: subir la lista propia siempre
            estuvo abierto a toda la sesión, y el rol acá sólo servía para
            dejar a un `member` con permiso de subir el archivo y sin permiso
            de crear la sucursal a la que ese archivo pertenece. */}
        <Card
          title="Mi sucursal"
          description="Sólo si tu comercio no publica en el SEPA y cargás tu propia lista."
          scroll
        >
          {merchantQuery.isLoading ? null : (
            <MerchantForm merchant={merchantQuery.data} />
          )}
        </Card>

        {/* Inviting is the owner's: the API answers 403 to a member. */}
        {user?.role === "owner" && (
          <div className="lg:col-span-2">
            <MembersCard />
          </div>
        )}
      </div>
    </Page>
  );
}

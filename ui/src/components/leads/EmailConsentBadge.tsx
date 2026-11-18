import { Badge } from "@/components/ui/badge";
import { emailConsentClass, emailConsentLabel } from "@/lib/emailConsent";
import { cn } from "@/lib/utils";

export function EmailConsentBadge({
    consent,
    className,
}: {
    consent: string | null | undefined;
    className?: string;
}) {
    return (
        <Badge variant="outline" className={cn(emailConsentClass(consent), className)}>
            {emailConsentLabel(consent)}
        </Badge>
    );
}

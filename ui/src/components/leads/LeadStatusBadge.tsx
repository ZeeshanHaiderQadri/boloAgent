import { Badge } from "@/components/ui/badge";
import { leadStatusClass, leadStatusLabel } from "@/lib/leadStatus";
import { cn } from "@/lib/utils";

export function LeadStatusBadge({
    status,
    className,
}: {
    status: string | null | undefined;
    className?: string;
}) {
    return (
        <Badge variant="outline" className={cn(leadStatusClass(status), className)}>
            {leadStatusLabel(status)}
        </Badge>
    );
}

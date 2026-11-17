"use client";

import {
    Download,
    FileJson,
    FileSpreadsheet,
    FileText,
    Image as ImageIcon,
    Loader2,
    Table2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { exportLeadsApiV1LeadsExportGet } from "@/client/sdk.gen";
import type { ExportLeadsApiV1LeadsExportGetData } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { detailFromError } from "@/lib/apiError";

type ExportFormat = "csv" | "xlsx" | "pdf" | "png" | "json";
type ExportFilters = NonNullable<ExportLeadsApiV1LeadsExportGetData["query"]>;

const FORMATS: {
    format: ExportFormat;
    label: string;
    hint: string;
    icon: typeof Download;
}[] = [
    { format: "csv", label: "CSV", hint: "Numbers, sheets, any tool", icon: Table2 },
    { format: "xlsx", label: "Excel (XLSX)", hint: "Formatted spreadsheet", icon: FileSpreadsheet },
    { format: "pdf", label: "PDF report", hint: "Share or print", icon: FileText },
    { format: "png", label: "PNG image", hint: "Paste into chat or docs", icon: ImageIcon },
    { format: "json", label: "JSON", hint: "Full records for developers", icon: FileJson },
];

export function ExportLeadsButton({
    filters,
    disabled = false,
    size = "default",
}: {
    filters: ExportFilters;
    disabled?: boolean;
    size?: "default" | "sm";
}) {
    const [exporting, setExporting] = useState<ExportFormat | null>(null);

    const handleExport = async (format: ExportFormat) => {
        setExporting(format);
        try {
            const response = await exportLeadsApiV1LeadsExportGet({
                query: { ...filters, format },
                parseAs: "blob",
            });

            if (response.error) {
                toast.error(detailFromError(response.error, "Export failed"));
                return;
            }
            if (!response.data) {
                toast.error("The export came back empty");
                return;
            }

            const disposition = response.response?.headers.get("content-disposition");
            const filename =
                disposition?.match(/filename="?([^"]+)"?/)?.[1] ??
                `bolo_leads.${format}`;
            const url = window.URL.createObjectURL(response.data as Blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            window.URL.revokeObjectURL(url);
            toast.success(`Exported ${filename}`);
        } catch (error) {
            console.error("Lead export failed:", error);
            toast.error("Export failed");
        } finally {
            setExporting(null);
        }
    };

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button variant="outline" size={size} disabled={disabled || exporting !== null}>
                    {exporting ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <Download className="h-4 w-4" />
                    )}
                    Export
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-60">
                <DropdownMenuLabel>Download current selection</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {FORMATS.map((item) => {
                    const Icon = item.icon;
                    return (
                        <DropdownMenuItem
                            key={item.format}
                            disabled={exporting !== null}
                            onSelect={() => void handleExport(item.format)}
                        >
                            <Icon className="h-4 w-4" />
                            <div className="flex flex-col">
                                <span>{item.label}</span>
                                <span className="text-xs text-muted-foreground">{item.hint}</span>
                            </div>
                        </DropdownMenuItem>
                    );
                })}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

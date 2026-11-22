"use client";

import {
    AlertTriangle,
    CheckCircle2,
    Download,
    FileSpreadsheet,
    Loader2,
    Upload,
    UserPlus,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import {
    downloadLeadImportTemplateApiV1LeadsImportTemplateGet,
    importLeadsApiV1LeadsImportPost,
    previewLeadImportApiV1LeadsImportPreviewPost,
} from "@/client/sdk.gen";
import type { LeadImportPreviewResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogClose,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";

const MAX_BYTES = 5 * 1024 * 1024;
const ACCEPT = ".xlsx,.csv";

type Busy = "template-xlsx" | "template-csv" | "preview" | "import" | null;

function downloadBlob(data: Blob, filename: string) {
    const url = window.URL.createObjectURL(data);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.URL.revokeObjectURL(url);
}

export function ImportLeadsButton({ onImported }: { onImported?: () => void }) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState<Busy>(null);
    const [file, setFile] = useState<File | null>(null);
    const [countryCode, setCountryCode] = useState("");
    const [preview, setPreview] = useState<LeadImportPreviewResponse | null>(null);

    const reset = () => {
        setFile(null);
        setPreview(null);
        setCountryCode("");
        setBusy(null);
        if (inputRef.current) inputRef.current.value = "";
    };

    const handleOpenChange = (next: boolean) => {
        if (!next && busy !== null) return;
        setOpen(next);
        if (!next) reset();
    };

    const downloadTemplate = async (format: "xlsx" | "csv") => {
        setBusy(`template-${format}`);
        try {
            const response =
                await downloadLeadImportTemplateApiV1LeadsImportTemplateGet({
                    query: { format },
                    parseAs: "blob",
                });
            if (response.error) {
                toast.error(detailFromError(response.error, "Template download failed"));
                return;
            }
            if (!response.data) {
                toast.error("The template came back empty");
                return;
            }
            const disposition = response.response?.headers.get("content-disposition");
            const filename =
                disposition?.match(/filename="?([^"]+)"?/)?.[1] ??
                `bolo_leads_template.${format}`;
            downloadBlob(response.data as Blob, filename);
        } catch (error) {
            console.error("Template download failed:", error);
            toast.error("Template download failed");
        } finally {
            setBusy(null);
        }
    };

    const chooseFile = (selected: File | null) => {
        setPreview(null);
        if (!selected) {
            setFile(null);
            return;
        }
        if (!/\.(xlsx|csv)$/i.test(selected.name)) {
            toast.error("Choose an .xlsx or .csv file");
            return;
        }
        if (selected.size > MAX_BYTES) {
            toast.error("That file is larger than 5 MB");
            return;
        }
        setFile(selected);
    };

    const body = () => ({
        file: file as File,
        country_code: countryCode.trim().toUpperCase() || undefined,
    });

    const runPreview = async () => {
        if (!file) return;
        setBusy("preview");
        setPreview(null);
        try {
            const response = await previewLeadImportApiV1LeadsImportPreviewPost({
                body: body(),
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not read that file"));
                return;
            }
            if (!response.data) {
                toast.error("The preview came back empty");
                return;
            }
            setPreview(response.data);
        } catch (error) {
            console.error("Lead import preview failed:", error);
            toast.error("Could not read that file");
        } finally {
            setBusy(null);
        }
    };

    const runImport = async () => {
        if (!file) return;
        setBusy("import");
        try {
            const response = await importLeadsApiV1LeadsImportPost({
                body: body(),
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Import failed"));
                return;
            }
            const result = response.data;
            if (!result) {
                toast.error("The import came back empty");
                return;
            }
            const notes = [
                result.duplicates > 0 ? `${result.duplicates} duplicate row(s)` : null,
                result.skipped > 0 ? `${result.skipped} row(s) skipped` : null,
            ]
                .filter(Boolean)
                .join(" · ");
            toast.success(
                `${result.created} added, ${result.updated} updated`,
                notes ? { description: notes } : undefined,
            );
            setOpen(false);
            reset();
            onImported?.();
        } catch (error) {
            console.error("Lead import failed:", error);
            toast.error("Import failed");
        } finally {
            setBusy(null);
        }
    };

    const recognized = preview
        ? Array.from(new Set(Object.values(preview.mapped_columns ?? {})))
        : [];
    const visibleWarnings = (preview?.warnings ?? []).slice(0, 8);

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogTrigger asChild>
                <Button variant="outline" title="Import leads from a spreadsheet">
                    <Upload className="h-4 w-4" />
                    Import
                </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <UserPlus className="h-5 w-5 text-cta" />
                        Import your own leads
                    </DialogTitle>
                    <DialogDescription>
                        Upload a spreadsheet of businesses you already know. Phone
                        numbers, emails, websites and social profiles become CRM leads
                        with the same WhatsApp links and icons as a Google Maps search.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-5">
                    <section className="rounded-xl border bg-muted/40 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <p className="text-sm font-medium">Start with the Bolo template</p>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    Keep the header row, delete the two example rows, and fill
                                    in one business per row.
                                </p>
                            </div>
                            <div className="flex gap-2">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    disabled={busy !== null}
                                    onClick={() => void downloadTemplate("xlsx")}
                                >
                                    {busy === "template-xlsx" ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <FileSpreadsheet className="h-4 w-4" />
                                    )}
                                    Excel template
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    disabled={busy !== null}
                                    onClick={() => void downloadTemplate("csv")}
                                >
                                    {busy === "template-csv" ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4" />
                                    )}
                                    CSV template
                                </Button>
                            </div>
                        </div>
                    </section>

                    <section className="space-y-3">
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="min-w-52 flex-1">
                                <Label htmlFor="lead-import-file">Your spreadsheet</Label>
                                <input
                                    ref={inputRef}
                                    id="lead-import-file"
                                    type="file"
                                    accept={ACCEPT}
                                    className="hidden"
                                    onChange={(event) =>
                                        chooseFile(event.target.files?.[0] ?? null)
                                    }
                                />
                                <Button
                                    type="button"
                                    variant="outline"
                                    className="mt-1.5 w-full justify-start"
                                    disabled={busy !== null}
                                    onClick={() => inputRef.current?.click()}
                                >
                                    <Upload className="h-4 w-4" />
                                    <span className="truncate">
                                        {file ? file.name : "Choose an .xlsx or .csv file"}
                                    </span>
                                </Button>
                            </div>
                            <div className="w-36">
                                <Label htmlFor="lead-import-country">
                                    Default country
                                </Label>
                                <Input
                                    id="lead-import-country"
                                    className="mt-1.5"
                                    placeholder="e.g. US"
                                    maxLength={2}
                                    value={countryCode}
                                    onChange={(event) =>
                                        setCountryCode(
                                            event.target.value.replace(/[^a-zA-Z]/g, ""),
                                        )
                                    }
                                />
                                <p className="mt-1 text-xs text-muted-foreground">
                                    Used when a row has no country.
                                </p>
                            </div>
                            <Button
                                type="button"
                                variant="secondary"
                                disabled={!file || busy !== null}
                                onClick={() => void runPreview()}
                            >
                                {busy === "preview" ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <CheckCircle2 className="h-4 w-4" />
                                )}
                                Preview
                            </Button>
                        </div>
                    </section>

                    {preview && (
                        <section className="space-y-3 rounded-xl border p-4">
                            <div className="flex flex-wrap items-center gap-2 text-sm">
                                <Badge variant="success">{preview.ready_rows} ready</Badge>
                                {preview.skipped_rows > 0 && (
                                    <Badge variant="outline">
                                        {preview.skipped_rows} skipped
                                    </Badge>
                                )}
                                {preview.duplicate_rows > 0 && (
                                    <Badge variant="secondary">
                                        {preview.duplicate_rows} duplicate
                                    </Badge>
                                )}
                                {(preview.existing_rows ?? 0) > 0 && (
                                    <Badge variant="outline">
                                        {preview.existing_rows} already in CRM
                                    </Badge>
                                )}
                                <span className="text-xs text-muted-foreground">
                                    {preview.total_rows} row(s) in {(preview.sheets ?? []).join(", ")}
                                </span>
                            </div>

                            {recognized.length > 0 && (
                                <p className="text-xs text-muted-foreground">
                                    Recognized columns: {recognized.slice(0, 12).join(", ")}
                                    {recognized.length > 12
                                        ? ` and ${recognized.length - 12} more`
                                        : ""}
                                </p>
                            )}

                            {visibleWarnings.length > 0 && (
                                <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
                                    <p className="flex items-center gap-2 text-xs font-medium">
                                        <AlertTriangle className="h-3.5 w-3.5" />
                                        Check these before importing
                                    </p>
                                    <ul className="mt-1.5 max-h-28 space-y-1 overflow-y-auto text-xs text-muted-foreground">
                                        {visibleWarnings.map((warning) => (
                                            <li key={warning}>{warning}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {(preview.leads ?? []).length > 0 && (
                                <div className="overflow-x-auto rounded-lg border">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Business</TableHead>
                                                <TableHead>Phone</TableHead>
                                                <TableHead>Email</TableHead>
                                                <TableHead>Socials</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead className="w-8" />
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {(preview.leads ?? []).map((lead) => (
                                                <TableRow key={`${lead.sheet}-${lead.row_number}`}>
                                                    <TableCell className="max-w-56">
                                                        <div className="flex flex-col">
                                                            <span className="truncate font-medium">
                                                                {lead.title ?? "—"}
                                                            </span>
                                                            <span className="truncate text-xs text-muted-foreground">
                                                                {[lead.city, lead.state]
                                                                    .filter(Boolean)
                                                                    .join(", ") || `Row ${lead.row_number}`}
                                                            </span>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="whitespace-nowrap text-sm">
                                                        {lead.phone_e164 ?? lead.phone ?? "—"}
                                                    </TableCell>
                                                    <TableCell className="max-w-48 truncate text-sm">
                                                        {lead.email ?? "—"}
                                                    </TableCell>
                                                    <TableCell className="text-xs text-muted-foreground">
                                                        {(lead.social_platforms ?? []).length > 0
                                                            ? (lead.social_platforms ?? []).join(", ")
                                                            : "—"}
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-sm capitalize">
                                                                {(lead.crm_status ?? "new").replace(/_/g, " ")}
                                                            </span>
                                                            {lead.existing && (
                                                                <Badge variant="outline">In CRM</Badge>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>
                                                        {(lead.issues ?? []).length > 0 && (
                                                            <AlertTriangle
                                                                className="h-4 w-4 text-amber-500"
                                                                aria-label={(lead.issues ?? []).join("; ")}
                                                            />
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}
                            <p className="text-xs text-muted-foreground">
                                {(preview.existing_rows ?? 0) > 0
                                    ? `${preview.existing_rows} of these already exist in your CRM and will be updated by phone, website or email.`
                                    : "Existing leads are updated by phone, website or email."}{" "}
                                Your CRM status, tags and notes are never overwritten.
                            </p>
                        </section>
                    )}
                </div>

                <DialogFooter>
                    <DialogClose asChild>
                        <Button variant="ghost" disabled={busy !== null}>
                            Cancel
                        </Button>
                    </DialogClose>
                    <Button
                        type="button"
                        disabled={!preview || preview.ready_rows === 0 || busy !== null}
                        onClick={() => void runImport()}
                    >
                        {busy === "import" ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Upload className="h-4 w-4" />
                        )}
                        {preview
                            ? `Import ${preview.ready_rows} lead${preview.ready_rows === 1 ? "" : "s"}`
                            : "Import"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

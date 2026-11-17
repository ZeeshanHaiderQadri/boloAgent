import type { LeadResponse } from "@/client/types.gen";

type MapsLead = Pick<
    LeadResponse,
    "title" | "address" | "city" | "state" | "country" | "latitude" | "longitude" | "place_id"
>;

/**
 * Link to the business's original Google Maps location.
 *
 * Coordinates identify the exact pin and a Google place id makes Maps open
 * the matching listing; when neither is available we fall back to a text
 * search built from the business name and address.
 */
export function leadMapsUrl(lead: MapsLead): string {
    if (lead.latitude != null && lead.longitude != null) {
        const coordinate = `${lead.latitude},${lead.longitude}`;
        const place = lead.place_id
            ? `&query_place_id=${encodeURIComponent(lead.place_id)}`
            : "";
        return `https://www.google.com/maps/search/?api=1&query=${coordinate}${place}`;
    }
    const fallback = [
        lead.title,
        lead.address,
        lead.city,
        lead.state,
        lead.country,
    ]
        .filter(Boolean)
        .join(", ");
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
        fallback || lead.title,
    )}`;
}

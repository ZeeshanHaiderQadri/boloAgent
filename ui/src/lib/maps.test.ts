import { describe, expect, it } from "vitest";

import { leadMapsUrl } from "./maps";

type MapsLead = Parameters<typeof leadMapsUrl>[0];

const base: MapsLead = {
    title: "Apex Coffee",
    address: "123 Market Street",
    city: "Sacramento",
    state: "CA",
    country: "US",
    latitude: null,
    longitude: null,
    place_id: null,
};

function url(overrides: Partial<MapsLead> = {}) {
    return leadMapsUrl({ ...base, ...overrides });
}

describe("leadMapsUrl", () => {
    it("opens the exact pin when coordinates are available", () => {
        expect(url({ latitude: 37.79, longitude: -122.4 })).toBe(
            "https://www.google.com/maps/search/?api=1&query=37.79,-122.4",
        );
    });

    it("adds the Google place id so Maps opens the right listing", () => {
        expect(
            url({ latitude: 37.79, longitude: -122.4, place_id: "abc 123" }),
        ).toBe(
            "https://www.google.com/maps/search/?api=1&query=37.79,-122.4&query_place_id=abc%20123",
        );
    });

    it("falls back to a name and address search without coordinates", () => {
        const result = new URL(url());
        expect(result.origin + result.pathname).toBe(
            "https://www.google.com/maps/search/",
        );
        expect(result.searchParams.get("query")).toBe(
            "Apex Coffee, 123 Market Street, Sacramento, CA, US",
        );
    });

    it("still produces a usable search when only the name exists", () => {
        const result = new URL(
            leadMapsUrl({
                title: "Apex Coffee",
                address: null,
                city: null,
                state: null,
                country: null,
                latitude: null,
                longitude: null,
                place_id: null,
            }),
        );
        expect(result.searchParams.get("query")).toBe("Apex Coffee");
    });
});

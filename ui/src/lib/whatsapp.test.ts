import { describe, expect, it } from "vitest";

import { describePhoneNumber } from "./whatsapp";

describe("describePhoneNumber", () => {
    it("formats a number under the country the backend resolved", () => {
        expect(describePhoneNumber("+15128522300", "US")).toEqual({
            formatted: "+1 (512) 852-2300",
            countryName: "United States",
        });
    });

    it("formats a non-NANP number with its own mask", () => {
        expect(describePhoneNumber("+442079460018", "GB")).toEqual({
            formatted: "+44 2079 460018",
            countryName: "United Kingdom",
        });
    });

    it("falls back to plain E.164 when the country is unknown or mismatched", () => {
        expect(describePhoneNumber("+15128522300", null)).toEqual({
            formatted: "+15128522300",
            countryName: null,
        });
        expect(describePhoneNumber("+15128522300", "GB")).toEqual({
            formatted: "+15128522300",
            countryName: null,
        });
    });
});

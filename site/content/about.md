---
title: Methodology
description: How the providers are compared, what the colours mean and where the data comes from.
weight: 20
---

**eMailCompare** compares email providers by who runs them, where their servers are, what their terms and privacy policies say, and what you have to hand over to register. It does not recommend any provider.

## Status of a provider

| Mark | Meaning |
|------|---------|
| ✅ | An account was registered and tested by hand |
| 🤖 | Data collected automatically (DNS, RDAP/WHOIS, the provider's website); registration not tested yet |
| ⏳ | Pending: partial data, not reviewed yet |
| ❌ | The service is gone: shut down, under permanent maintenance or unreachable |

## Comparison criteria

- **Jurisdiction**: country of the MX (mail) servers by GeoIP. For a domain without MX records, the country of its web server.
- **Intelligence alliance**: Five Eyes (United States, United Kingdom, Canada, Australia, New Zealand), Nine Eyes (adds Denmark, France, the Netherlands, Norway) and Fourteen Eyes (adds Germany, Belgium, Italy, Spain, Sweden), assigned from the jurisdiction.
- **Domain created / expires**: from RDAP or WHOIS. Domains registered in 2010 or earlier are green, domains from 2021 on are red.
- **Registration requirements**: whether signing up asks for an email to verify, a phone number or SMS code, or personal data such as name and address. Filled only after a manual test (✅).
- **Terms of service and privacy policy**: a short summary. Summaries of collected rows are keyword-based; follow the provider's page for the full text.
- **Infrastructure**: A and MX records with the hosting network and its country.

## Colours

- **Green**: favourable — an old domain, no phone or personal data needed.
- **Yellow**: caution — optional data, Nine Eyes.
- **Red**: unfavourable — a very new domain, Five Eyes, required data.
- **Purple**: Fourteen Eyes.
- **Grey**: the service is gone.

## Data

All data is public: DNS, RDAP/WHOIS, GeoIP and the providers' own websites. It may be outdated or wrong. The source is [emails.csv](https://github.com/cyberanchor/eMailCompare/blob/main/emails.csv) in the repository; this site is generated from it with Hugo. Corrections are welcome in [Issues](https://github.com/cyberanchor/eMailCompare/issues).

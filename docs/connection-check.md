# Partner API connection check

This file records the first non-publishing integration check. The workflow runs unit tests, then queries the Takprodam Publisher API only when `TAKPRODAM_API_TOKEN` is configured. A push-triggered run never publishes to Telegram because `PUBLISH_ENABLED` is false for push events. Do not store API tokens in the repository.

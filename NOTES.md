# test-pyicloud.py Documentation

## Purpose
This test script is designed to validate the functionality of the PyiCloud library's integration with various iCloud services, specifically:
- Basic authentication and 2FA handling
- Calendar service access
- Reminders service (with CloudKit integration)
- Contacts service

## Current Implementation

### Authentication Flow
1. Initial authentication using username/password
2. 2FA handling with verification code support
3. Session trust establishment
4. Service-specific authentication for each service type

### Service-Specific Implementations

#### Contacts Service
- Status: ✅ Working
- Implementation: Uses basic authentication
- Notes: Most reliable service as it doesn't require special token handling

#### Calendar Service
- Status: ❌ Not Working (400 Bad Request)
- Current Approach:
  - Force authentication refresh before service initialization
  - Custom headers including session tokens and timezone information
  - Cookie handling through header injection
- Issues:
  - Getting 400 Bad Request errors despite proper authentication
  - May need additional service-specific initialization steps

#### Reminders Service
- Status: ❌ Not Working (400 Bad Request)
- Current Approach:
  - CloudKit token acquisition
  - Multi-step initialization process
  - Retry mechanism with exponential backoff
- Issues:
  - CloudKit token acquisition failing
  - Possible missing or incorrect headers/parameters

## Attempted Approaches

### Cookie Handling
1. ❌ Direct cookie jar access (`get_dict()`) - Failed due to LWPCookieJar incompatibility
2. ✅ Manual cookie dictionary creation from cookie jar
3. ❌ Cookie passing through requests session - Didn't persist properly

### CloudKit Authentication
1. ❌ Direct service initialization without CloudKit - Failed with 401
2. ❌ Basic CloudKit token request - Failed with 400
3. 🔄 Current: Enhanced CloudKit initialization with proper headers and retry mechanism

### Calendar Service
1. ❌ Basic service initialization - Failed with 401
2. ❌ Service-specific authentication - Failed with 400
3. 🔄 Current: Enhanced initialization with proper headers and timezone handling

## What Hasn't Worked

1. Direct service access without proper initialization
2. Using requests session cookies without explicit header setting
3. Basic CloudKit token requests without proper headers
4. Calendar service initialization without timezone information
5. Trying to use the same headers for all services

## What Has Worked

1. Basic authentication and 2FA handling
2. Contacts service access
3. Cookie handling through manual dictionary creation
4. Logging improvements and error handling
5. Calendar and Reminders services with proper authentication:
   - Added service-specific headers (X-Apple-Auth-Token, X-Apple-Domain-Id)
   - Added service-specific parameters (clientBuildNumber, clientMasteringNumber, dsid)
   - No CloudKit token required for these services

## Future Ideas

### Short Term
1. Investigate web interface requests to capture exact header patterns
2. Try different CloudKit container identifiers
3. Implement service-specific error handling
4. Add request/response logging for debugging

### Medium Term
1. Implement proper session persistence
2. Add automatic token refresh
3. Improve error recovery mechanisms
4. Add rate limiting and request throttling

### Long Term
1. Consider implementing a mock iCloud server for testing
2. Add comprehensive integration tests
3. Consider splitting into separate test suites per service
4. Add performance benchmarking

## Known Issues

1. urllib3 SSL warning with LibreSSL
2. ~~CloudKit token acquisition failing~~
3. ~~Calendar service initialization failing~~
4. ~~Possible session persistence issues~~

## Debug Tips

1. Enable debug logging with `--debug` flag
2. Check response content for error details
3. Verify session tokens are present
4. Monitor cookie state between requests

## References

1. [iCloud Web Interface Documentation](https://www.apple.com/icloud/)
2. [CloudKit Web Services Reference](https://developer.apple.com/library/archive/documentation/DataManagement/Conceptual/CloudKitWebServicesReference/index.html)
3. [PyiCloud Documentation](https://pyicloud.readthedocs.io/)

## Contributing

When making changes:
1. Document new approaches in this file
2. Update the "What Hasn't Worked" section if trying new methods
3. Keep track of successful changes in "What Has Worked"
4. Add any new debug tips or known issues

# Development Notes

## Running Tests

### Integration Tests
To run the integration tests for the reminders service:
```bash
python -m pytest tests_integration/test_reminders_integration.py -v -s
```

Requirements:
1. Set up environment variables:
   - `ICLOUD_USERNAME`: Your iCloud account email
   - `ICLOUD_PASSWORD`: Your iCloud account password
2. Be prepared to handle 2FA if required
3. Have an active iCloud account with reminders enabled

### Test Structure
The reminders integration tests verify:
1. Basic service functionality (listing reminders and collections)
2. List management (create, update, get lists)
3. Reminder lifecycle (create, update, verify attributes)
4. Priority levels and tags
5. Due dates and descriptions

Note: Reminder completion is not tested as it's not supported by the web API.

## Reminders Service

The reminders service provides access to iCloud reminders through the web API. Here are the supported features and limitations:

## Supported Features

### List Management
- Listing all reminder lists
- Creating new reminder lists
- Updating list titles and colors
- Retrieving list details by GUID

### Reminder Management
- Creating new reminders
- Updating existing reminders
- Setting and updating reminder titles and descriptions
- Setting and updating due dates
- Setting and updating priority levels (0-4)
  - 0: None
  - 1: Low
  - 2: Medium
  - 3: High
  - 4: Urgent
- Adding and updating tags for reminders
- Organizing reminders into lists

## Limitations

### Completion Status
- The iCloud web API does not support completing reminders
- Reminder completion is only available through native iOS/macOS apps
- The `complete()` method will always return `False`

### Other Limitations
- No support for recurring reminders through the web API
- No support for reminder alarms/notifications
- No support for deleting reminders or lists
- No support for subtasks/nested tasks
- No support for sharing reminders with family members

## Authentication

The reminders service requires proper authentication with iCloud. The service will:
1. Re-authenticate when necessary
2. Use appropriate headers for the reminders service
3. Maintain session cookies and tokens

## API Format

### Creating/Updating Lists
Lists are managed through the `/rd/collections` endpoint with the following data format:
```json
{
    "Collection": {
        "title": "List Title",
        "guid": "unique-guid",
        "ctag": null,
        "color": null,
        "order": null,
        "symbolicColor": null,
        "lastModifiedDate": "ISO-8601-date",
        "createdDate": "ISO-8601-date"
    }
}
```

### Creating/Updating Reminders
Reminders are managed through the `/rd/reminders/tasks` endpoint with the following data format:
```json
{
    "Reminder": {
        "title": "Reminder Title",
        "description": "Description",
        "guid": "unique-guid",
        "pGuid": "parent-list-guid",
        "etag": null,
        "order": null,
        "priority": 0,
        "tags": ["tag1", "tag2"],
        "recurrence": null,
        "alarms": [],
        "createdDate": "ISO-8601-date",
        "lastModifiedDate": "ISO-8601-date",
        "dueDateIsAllDay": false,
        "completed": false,
        "completedDate": null
    }
}
```

## Best Practices

1. Always refresh the cache after creating or updating reminders/lists
2. Use appropriate error handling for API requests
3. Verify list existence before creating reminders in a list
4. Use ISO-8601 format for dates
5. Keep priority levels between 0-4
6. Use meaningful tags for better organization

## Authentication

The iCloud web API requires proper authentication for each service. For reminders, this includes:
- Re-authenticating with `authenticate(True, "reminders")` before making requests
- Including the `X-Apple-Auth-Token` header from cookies
- Setting the correct service headers (`X-Apple-Service`, `X-Apple-Domain-Id`)

# Reminders Service Debugging Notes

## Current Issues
1. Authentication failures with 503 errors
2. Tests timing out due to long execution times
3. System incorrectly reporting "no lists" despite lists being present in output

## Attempted Solutions
1. Added authentication retry logic with exponential backoff
   - Result: Still seeing 503 errors and auth failures
   - Possible issue: Backoff timing may need adjustment

2. Modified update method to verify changes
   - Result: Update verification failing
   - Possible issue: Race condition between update and verification

3. Added service-specific parameters from calendar service
   - Result: Partially successful - can list reminders but updates fail
   - Note: May need additional parameters for update operations

## Failed Approaches
1. Simple retry mechanism without backoff
   - Why it failed: Rate limiting from iCloud service
   - Lesson: Need smarter retry strategy

2. Direct parameter copying from calendar service
   - Why it failed: Reminders service needs some unique parameters
   - Lesson: Need to understand service-specific requirements

## Current Hypotheses
1. Authentication token expiration
   - The service may be invalidating tokens more aggressively than expected
   - Need to implement token refresh before each operation

2. Race conditions
   - Server may need more time to process updates
   - Consider implementing consistent wait times between operations

3. Missing parameters
   - Update operations may require additional headers/parameters
   - Need to capture and compare successful vs failed requests

## Next Steps
1. Implement token refresh before each operation
2. Add consistent wait times between operations
3. Review successful calendar service operations to identify missing parameters
4. Consider implementing request/response logging for debugging
5. Optimize test execution time by reducing retry attempts for known failure cases

## Performance Issues
1. Tests taking too long
   - Current timeout: None specified
   - Retry delays compound the issue
   - Need to optimize retry strategy and add timeouts

## Questions to Investigate
1. Why does list retrieval work but updates fail?
2. Are we handling rate limiting correctly?
3. Is there a difference in how the web interface handles these operations?
4. What's the minimum set of parameters needed for each operation type?

## New Approach (2024-03-25)

### Authentication Improvements
1. Implement pre-request token refresh
2. Add service-specific token expiration tracking
3. Separate auth flows for reminders vs other services

### Performance Optimizations
1. Add controlled concurrency with ThreadPoolExecutor
2. Implement jittered backoff for retries
3. Add rate limiting between batch operations

### Validation Added
1. Collection existence checks before operations
2. Timezone-aware date handling
3. Default collection fallback

## Expected Results
1. test_reminder_lifecycle: Fix 500 errors with fresh tokens
2. test_large_list_performance: Reduce time from 165s to <30s
3. test_chief_of_staff_operations: Fix date comparison issues
4. test_reminder_error_cases: Proper collection fallback

## Verification Plan
1. Run tests with `--log-cli-level=DEBUG` to monitor auth flows
2. Check response times in performance test
3. Verify UTC dates in network traces
4. Test invalid collection handling

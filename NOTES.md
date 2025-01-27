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

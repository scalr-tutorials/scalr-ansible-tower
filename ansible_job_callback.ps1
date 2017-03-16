#!powershell

add-type @"
    using System.Net;
    using System.Security.Cryptography.X509Certificates;
    public class TrustAllCertsPolicy : ICertificatePolicy {
        public bool CheckValidationResult(
            ServicePoint srvPoint, X509Certificate certificate,
            WebRequest request, int certificateProblem) {
            return true;
        }
    }
"@
[System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

$data = @{
    "host_config_key"=$env:ANSIBLE_CONFIG_KEY
}

$headers = @{
    "Accept"="*/*"
}

Invoke-WebRequest -Uri $env:ANSIBLE_CALLBACK_URL -Method POST -Body $data -UseBasicParsing -Headers $headers

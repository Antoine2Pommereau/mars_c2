"""Preuves images : signature des adresses R2, géoréférence des vignettes VIIRS."""
from datetime import datetime, timezone

from mars.r2_signature import presign


def test_presigned_url_matches_the_aws_documentation_example():
    """Exemple « Authenticating Requests: Using Query Parameters (AWS Signature Version 4) » de la documentation S3."""
    url = presign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", "AKIAIOSFODNN7EXAMPLE",
                  "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1", expires=86400,
                  now=datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
    assert "X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request" in url


def test_presigned_url_agrees_with_boto3_for_r2_path_style():
    """Même signature que boto3 (qui écrit sur R2 dans le conteneur taches), à la date qu'il a signée."""
    import boto3
    from botocore.config import Config
    s3 = boto3.client("s3", endpoint_url="https://compte.eu.r2.cloudflarestorage.com", region_name="auto",
                      aws_access_key_id="cle", aws_secret_access_key="secret",
                      config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    ref = s3.generate_presigned_url("get_object", Params={"Bucket": "mars-c2", "Key": "vignettes/viirs/x/1.png"},
                                    ExpiresIn=300)
    q = dict(p.split("=", 1) for p in ref.split("?", 1)[1].split("&"))
    when = datetime.strptime(q["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    ours = presign("GET", "compte.eu.r2.cloudflarestorage.com", "/mars-c2/vignettes/viirs/x/1.png", "cle", "secret",
                   expires=300, now=when)
    assert ours.split("?")[0] == ref.split("?")[0]
    assert dict(p.split("=", 1) for p in ours.split("?", 1)[1].split("&")) == q

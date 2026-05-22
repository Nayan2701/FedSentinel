NETWORK="infra_fedsentinel_net"

docker run --rm -it \
  --network "${NETWORK}" \
  -v "$(pwd)/scripts:/opt/spark/scripts" \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e AWS_REGION=us-east-1 \
  apache/spark:3.5.1 \
  /opt/spark/bin/spark-sql \
  --conf spark.jars.ivy=/tmp/.ivy2 \
  --packages \
org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,org.projectnessie.nessie-integrations:nessie-spark-extensions-3.5_2.12:0.94.0,software.amazon.awssdk:bundle:2.20.160,software.amazon.awssdk:url-connection-client:2.20.160 \
  --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions,org.projectnessie.spark.extensions.NessieSparkSessionExtensions \
  --conf spark.sql.catalog.nessie=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.nessie.uri=http://fedsentinel-nessie:19120/api/v2 \
  --conf spark.sql.catalog.nessie.ref=main \
  --conf spark.sql.catalog.nessie.authentication.type=NONE \
  --conf spark.sql.catalog.nessie.catalog-impl=org.apache.iceberg.nessie.NessieCatalog \
  --conf spark.sql.catalog.nessie.warehouse=s3://fedsentinel-warehouse/ \
  --conf spark.sql.catalog.nessie.io-impl=org.apache.iceberg.aws.s3.S3FileIO \
  --conf spark.sql.catalog.nessie.s3.endpoint=http://fedsentinel-minio:9000 \
  --conf spark.sql.catalog.nessie.s3.path-style-access=true \
  --conf spark.sql.catalog.nessie.s3.access-key-id=minioadmin \
  --conf spark.sql.catalog.nessie.s3.secret-access-key=minioadmin
EOF

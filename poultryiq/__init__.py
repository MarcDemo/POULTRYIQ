"""Project package initialization."""

# Namecheap shared hosting blocks the compiler required by mysqlclient.
# PyMySQL provides the same MySQLdb interface without a native extension.
import pymysql

pymysql.version_info = (2, 2, 1, "final", 0)
pymysql.install_as_MySQLdb()

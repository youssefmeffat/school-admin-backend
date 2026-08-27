using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;
using Microsoft.Extensions.Configuration;
using System.IO;

using Pomelo.EntityFrameworkCore.MySql.Infrastructure;

namespace Infrastructure.Data
{
    public class DesignTimeDbContextFactory : IDesignTimeDbContextFactory<SchoolDbContext>
    {
        public SchoolDbContext CreateDbContext(string[] args)
        {
            var optionsBuilder = new DbContextOptionsBuilder<SchoolDbContext>();

            // Try to load configuration from the Api project (when using -s Api)
            var basePath = Path.Combine(Directory.GetCurrentDirectory(), "..", "Api");
            var configBuilder = new ConfigurationBuilder()
                .SetBasePath(Directory.Exists(basePath) ? basePath : Directory.GetCurrentDirectory())
                .AddJsonFile("appsettings.json", optional: true);

            var configuration = configBuilder.Build();
            var mysqlConn = configuration.GetConnectionString("DefaultConnection");
            if (!string.IsNullOrWhiteSpace(mysqlConn))
            {
                optionsBuilder.UseMySql(mysqlConn, ServerVersion.AutoDetect(mysqlConn));
            }
            else
            {
                var sqlite = configuration.GetConnectionString("SqliteConnection") ?? "Data Source=school.db";
                optionsBuilder.UseSqlite(sqlite);
            }

            return new SchoolDbContext(optionsBuilder.Options);
        }
    }
}

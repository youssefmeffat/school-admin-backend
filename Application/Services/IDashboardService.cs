namespace Application.Services
{
    public interface IDashboardService
    {
        Task<object> GetStatsAsync();
    }
}

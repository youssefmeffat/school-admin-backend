namespace Application.Common
{
    public class PaginationParams
    {
        private const int DefaultPageSize = 20;
        private const int MaxPageSize = 100;

        private int _page = 1;
        private int _pageSize = DefaultPageSize;

        public int Page
        {
            get => _page;
            set => _page = value < 1 ? 1 : value;
        }

        public int PageSize
        {
            get => _pageSize;
            set => _pageSize = value < 1 ? DefaultPageSize : Math.Min(value, MaxPageSize);
        }

        public int Skip => (Page - 1) * PageSize;
    }
}
